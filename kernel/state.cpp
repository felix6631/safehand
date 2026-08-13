#include "state.hpp"
#include "vendor/picosha2.h"

using json = nlohmann::json;

// 실행 계층이 보낸 "증언서(attestation)"를 파싱해서 구조체로 꺼낸다.
// AI(지능 계층)의 자기 진술(claimed_state)과 달리, 이건 실제로 화면을 붙잡고
// 있는 실행 계층이 보낸 "진짜 데이터"이며, rules.cpp의 R5가 이걸 신뢰의 기준으로 삼는다.
AttestationResult parse_attestation(const json& in) {
    AttestationResult result;

    if (!in.contains("attestation") || !in["attestation"].is_object()) {
        result.ok = false;
        result.detail = "attestation이 없거나 객체가 아닙니다";
        return result;
    }
    const json& a = in["attestation"];
    Attestation att;
    att.att_version = a.value("att_version", "");
    att.state_hash = a.value("state_hash", "");     // 화면 내용을 요약한 지문 -> claimed_state와 대조됨
    att.hmac = a.value("hmac", "");     // 위조 방지 서명값 -> 아래 hmac_sha256_hex로 재검증됨

    // state_view: 지금 실제 화면에 무엇이 보이는지 담은 핵심 데이터
    if (!a.contains("state_view") || !a["state_view"].is_object()) {
        result.ok = false;
        result.detail = "attestation.state_view가 없습니다";
        return result;
    }
    const json& sv = a["state_view"];
    att.state_view.raw = sv; // 원본 그대로도 보관 (HMAC 재계산, evidence 경로 탐색용)
    att.state_view.page = sv.value("page", "");

    // 실제 화면에 표시된 잔액 ? rules.cpp R3(금액 한도)가 AI 주장이 아닌 이 값을 기준으로 검사
    if (sv.contains("balance") && sv["balance"].is_number_integer()) {
        att.state_view.balance = sv["balance"].get<long long>();
    }

    // 실제 화면에 등록되어 있는 수취인 목록 ? R4(미등록 수취인)가 참조
    if (sv.contains("registered_payees") && sv["registered_payees"].is_array()) {
        for (const auto& p : sv["registered_payees"]) {
            if (p.is_string()) att.state_view.registered_payees.push_back(p.get<std::string>());
        }
    }

    // 실제 화면에서 입력 가능한 필드 목록
    if (sv.contains("form_fields") && sv["form_fields"].is_array()) {
        for (const auto& f : sv["form_fields"]) {
            if (f.is_string()) att.state_view.form_fields.push_back(f.get<std::string>());
        }
    }

    att.present = true;
    result.attestation = att;
    return result;
}

namespace {

// 문자열을 SHA-256으로 해싱해 "원본 바이트"(16진수 문자열이 아님) 그대로 반환.
// HMAC 계산 내부에서만 쓰이는 저수준 헬퍼.
std::string sha256_raw(const std::string& msg) {
    std::string digest(picosha2::k_digest_size, '\0');
    picosha2::hash256(msg, digest);
    return digest;
}

} // namespace

// RFC 2104 HMAC-SHA256. 64바이트(SHA-256 블록 크기)보다 긴 키는 다이제스트의
// "원본 바이트"로 축약해야 한다 ? hex 문자열로 축약하면 Python hmac 모듈과 결과가 어긋난다.
//
// 목적: 실행 계층만 아는 비밀 열쇠(key)로 state_view에 서명을 만든다.
// 커널은 같은 열쇠로 이 함수를 다시 호출해서 재계산한 값과 전달받은 hmac이
// 일치하는지 비교함으로써, "이 증언이 진짜 실행 계층에서 왔는지"(위조 여부)를 검증한다.
// 비밀 열쇠를 모르는 AI는 이 서명을 절대 위조할 수 없다.
std::string hmac_sha256_hex(const std::string& key, const std::string& msg) {
    constexpr size_t B = 64;
    std::string k = key;
    if (k.size() > B) {
        k = sha256_raw(k); // 64바이트보다 길면 해시로 축약
    }
    k.resize(B, '\0'); // 64바이트보다 짧으면 뒤에 0을 채워 정확히 64바이트로 맞춤

    // 표준 HMAC 절차: 열쇠를 두 개의 고정 패턴(0x36, 0x5c)과 XOR해서
    // 서로 다른 "믹싱된 열쇠" ipad/opad를 만든다
    std::string ipad(B, '\x36');
    std::string opad(B, '\x5c');
    for (size_t i = 0; i < B; i++) {
        ipad[i] = static_cast<char>(static_cast<unsigned char>(ipad[i]) ^ static_cast<unsigned char>(k[i]));
        opad[i] = static_cast<char>(static_cast<unsigned char>(opad[i]) ^ static_cast<unsigned char>(k[i]));
    }

    // 내용물을 ipad와 먼저 해싱(내부 다이제스트), 그 결과를 다시 opad와 해싱(최종 서명)
    std::string inner_digest = sha256_raw(ipad + msg);
    return picosha2::hash256_hex_string(opad + inner_digest);
}

// path 문자열이 가리키는 JSON 내부의 값을 찾아서 문자열로 변환해 반환한다.
// path 예시: "account.balance", "items[2].name" 처럼 점(.)과 대괄호([])로
// 객체 키와 배열 인덱스를 순서대로 표현한다.
// rules.cpp R5에서 "AI가 evidence로 댄 경로"를 실제 state_view 안에서 따라가
// 진짜 값을 찾아 AI가 입력하려는 값과 비교하는 데 쓰인다.
EvidenceResult resolve_evidence(const nlohmann::json& root, const std::string& path) {
    EvidenceResult result;
    if (path.empty()) return result;

    const json* cur = &root; // 현재 탐색 중인 JSON 노드를 가리키는 포인터
    size_t i = 0;
    while (i < path.size()) {
        // '.'나 '[' 가 나올 때까지 읽어서 하나의 키 이름을 뽑아낸다
        size_t start = i;
        while (i < path.size() && path[i] != '.' && path[i] != '[') i++;
        std::string key = path.substr(start, i - start);
        if (!key.empty()) {
            // 현재 노드가 객체가 아니거나 그 키가 없으면 경로 탐색 실패
            if (!cur->is_object() || !cur->contains(key)) return result;
            cur = &(*cur)[key]; // 한 단계 안으로 들어감
        }
        // '[' 로 시작하는 배열 인덱스들을 연속으로 처리 (예: "[2][0]" 같은 경우도 대응)
        while (i < path.size() && path[i] == '[') {
            size_t close = path.find(']', i);
            if (close == std::string::npos) return result; // 닫는 괄호 없으면 실패
            std::string idx_str = path.substr(i + 1, close - i - 1);
            int idx;
            try {
                size_t consumed = 0;
                idx = std::stoi(idx_str, &consumed);
                if (consumed != idx_str.size()) return result; // 숫자 뒤에 이상한 문자 있으면 실패
            } catch (...) {
                return result;
            }
            if (!cur->is_array() || idx < 0 || static_cast<size_t>(idx) >= cur->size()) return result;
            cur = &(*cur)[static_cast<size_t>(idx)];
            i = close + 1;
        }
        if (i < path.size() && path[i] == '.') i++; // 다음 키를 위해 점(.) 건너뛰기
    }

    // 경로 끝까지 도달한 값을 타입에 맞춰 문자열로 변환
    result.found = true;
    if (cur->is_string()) {
        result.value_str = cur->get<std::string>();
    } else if (cur->is_boolean()) {
        result.value_str = cur->get<bool>() ? "true" : "false";
    } else if (cur->is_number_integer()) {
        result.value_str = std::to_string(cur->get<long long>());
    } else if (cur->is_number_float()) {
        result.value_str = std::to_string(cur->get<double>());
    } else {
        result.value_str = cur->dump(); // 객체/배열이면 그냥 JSON 문자열로
    }
    return result;
}
