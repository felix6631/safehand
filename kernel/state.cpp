#include "state.hpp"
#include "vendor/picosha2.h"

using json = nlohmann::json;

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
    att.state_hash = a.value("state_hash", "");
    att.hmac = a.value("hmac", "");

    if (!a.contains("state_view") || !a["state_view"].is_object()) {
        result.ok = false;
        result.detail = "attestation.state_view가 없습니다";
        return result;
    }
    const json& sv = a["state_view"];
    att.state_view.raw = sv;
    att.state_view.page = sv.value("page", "");
    if (sv.contains("balance") && sv["balance"].is_number_integer()) {
        att.state_view.balance = sv["balance"].get<long long>();
    }
    if (sv.contains("registered_payees") && sv["registered_payees"].is_array()) {
        for (const auto& p : sv["registered_payees"]) {
            if (p.is_string()) att.state_view.registered_payees.push_back(p.get<std::string>());
        }
    }
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

std::string sha256_raw(const std::string& msg) {
    std::string digest(picosha2::k_digest_size, '\0');
    picosha2::hash256(msg, digest);
    return digest;
}

} // namespace

// RFC 2104 HMAC-SHA256. 64바이트(SHA-256 블록 크기)보다 긴 키는 다이제스트의
// "원본 바이트"로 축약해야 한다 — hex 문자열로 축약하면 Python hmac 모듈과 결과가 어긋난다.
std::string hmac_sha256_hex(const std::string& key, const std::string& msg) {
    constexpr size_t B = 64;
    std::string k = key;
    if (k.size() > B) {
        k = sha256_raw(k);
    }
    k.resize(B, '\0');

    std::string ipad(B, '\x36');
    std::string opad(B, '\x5c');
    for (size_t i = 0; i < B; i++) {
        ipad[i] = static_cast<char>(static_cast<unsigned char>(ipad[i]) ^ static_cast<unsigned char>(k[i]));
        opad[i] = static_cast<char>(static_cast<unsigned char>(opad[i]) ^ static_cast<unsigned char>(k[i]));
    }

    std::string inner_digest = sha256_raw(ipad + msg);
    return picosha2::hash256_hex_string(opad + inner_digest);
}

EvidenceResult resolve_evidence(const nlohmann::json& root, const std::string& path) {
    EvidenceResult result;
    if (path.empty()) return result;

    const json* cur = &root;
    size_t i = 0;
    while (i < path.size()) {
        size_t start = i;
        while (i < path.size() && path[i] != '.' && path[i] != '[') i++;
        std::string key = path.substr(start, i - start);
        if (!key.empty()) {
            if (!cur->is_object() || !cur->contains(key)) return result;
            cur = &(*cur)[key];
        }
        while (i < path.size() && path[i] == '[') {
            size_t close = path.find(']', i);
            if (close == std::string::npos) return result;
            std::string idx_str = path.substr(i + 1, close - i - 1);
            int idx;
            try {
                size_t consumed = 0;
                idx = std::stoi(idx_str, &consumed);
                if (consumed != idx_str.size()) return result;
            } catch (...) {
                return result;
            }
            if (!cur->is_array() || idx < 0 || static_cast<size_t>(idx) >= cur->size()) return result;
            cur = &(*cur)[static_cast<size_t>(idx)];
            i = close + 1;
        }
        if (i < path.size() && path[i] == '.') i++;
    }

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
        result.value_str = cur->dump();
    }
    return result;
}
