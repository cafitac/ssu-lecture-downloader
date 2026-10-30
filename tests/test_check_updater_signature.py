import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_updater_signature_under_test", REPO_ROOT / "scripts" / "check_updater_signature.py")
checker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)

# Output of `tauri signer generate` / `tauri signer sign` with two throwaway keys (public data only).
THROWAWAY_PUBKEY_A = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDgyMjg4Q0VBMTg2QzZCOEMKUldTTWEyd1k2b3dvZ3ZUcjlzZGRXNFZXWDZUc1JSdTFWYkMvYlZYd3pFald4R0l0Z2M4aFRuSzYK"
THROWAWAY_SIGNATURE_A = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVTTWEyd1k2b3dvZ2dacHpZcHZkN1Q0QldtNHZnVytQY2JjR25HNnZ1OEtiaUU0a1dHMzFkcFdzTGN6Zm9MMGNsQXRneGxyS05MZVFkUVZjQkkyNWZvbjVCczVQS0M2RWdvPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzkzMjk2Nzg1CWZpbGU6YS50YXIuZ3oKZk9ubHJZKzlZRWVERGpWajdsZVI3WjI1MVJoeUd3YUkwRjI2UEsxQlBVQmF5Ym5ITUg1LzVieXVpNnlUdnNVVTVVRlpjRkxnRk5BQllZRXpvQXFBQmc9PQo="
THROWAWAY_SIGNATURE_B = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVRb2Q5QWdyK3VsNzIvdGpxSmtaM2Z4ckl2MmtRcHFGTVR3VVVkNkZJcjlDRGpJTWRJdTVmOXZRdUFFY015dkpGeHJWK0hHZGFwRkxoczhMazg1WkhJR1ByalN2MGUwemdvPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzkzMjk2Nzg1CWZpbGU6YS50YXIuZ3oKTERxUEFmNEZ4WUtRWGlyTXExMFlXZU5CU0VQT3dCUzdCb2hlRXAraFhKc2dnVmp1dGw3c08rWVFuZ0d2SllGSmRQSjZyS1FSaEU2LzhnUzU1d1JaRHc9PQo="
KEY_ID_A = "8c6b6c18ea8c2882"
KEY_ID_B = "2877d020afeba5ef"


def b64(data: bytes | str) -> str:
    return base64.b64encode(data.encode() if isinstance(data, str) else data).decode()


def synthetic_pubkey(key_id: bytes) -> str:
    return b64("untrusted comment: minisign public key\n" + b64(b"Ed" + key_id + bytes(32)) + "\n")


def synthetic_signature(key_id: bytes) -> str:
    return b64(
        "untrusted comment: signature from tauri secret key\n"
        + b64(b"Ed" + key_id + bytes(64))
        + "\ntrusted comment: timestamp:1\n"
        + b64(bytes(64))
        + "\n"
    )


def conf(pubkey: str) -> dict:
    return {"plugins": {"updater": {"pubkey": pubkey}}}


def latest(signature: str, platforms=checker.DEFAULT_PLATFORMS) -> dict:
    return {"version": "0.2.0", "platforms": {p: {"signature": signature, "url": "https://example.invalid/x"} for p in platforms}}


def test_key_ids_of_real_tauri_output():
    assert checker.minisign_key_id(THROWAWAY_PUBKEY_A) == KEY_ID_A
    assert checker.minisign_key_id(THROWAWAY_SIGNATURE_A) == KEY_ID_A
    assert checker.minisign_key_id(THROWAWAY_SIGNATURE_B) == KEY_ID_B


def test_matching_key_passes_for_both_platform_keys():
    assert checker.check(latest(THROWAWAY_SIGNATURE_A), conf(THROWAWAY_PUBKEY_A)) == []
    assert checker.check(latest(THROWAWAY_SIGNATURE_A + "\n"), conf(THROWAWAY_PUBKEY_A)) == []


def test_signature_from_another_key_fails_each_platform():
    problems = checker.check(latest(THROWAWAY_SIGNATURE_B), conf(THROWAWAY_PUBKEY_A))
    assert problems == [
        f"darwin-x86_64: signed with key id {KEY_ID_B}, the app trusts {KEY_ID_A}",
        f"darwin-x86_64-app: signed with key id {KEY_ID_B}, the app trusts {KEY_ID_A}",
    ]


def test_synthetic_files_follow_the_same_rule():
    pubkey = synthetic_pubkey(bytes(range(8)))
    assert checker.check(latest(synthetic_signature(bytes(range(8)))), conf(pubkey)) == []
    assert len(checker.check(latest(synthetic_signature(bytes(range(1, 9)))), conf(pubkey))) == 2


def test_missing_platform_or_signature_is_reported():
    pubkey = THROWAWAY_PUBKEY_A
    assert checker.check(latest(THROWAWAY_SIGNATURE_A, ["darwin-x86_64"]), conf(pubkey)) == ["darwin-x86_64-app: no signature"]
    assert checker.check(latest(""), conf(pubkey)) == ["darwin-x86_64: no signature", "darwin-x86_64-app: no signature"]
    assert checker.check({}, conf(pubkey)) == ["darwin-x86_64: no signature", "darwin-x86_64-app: no signature"]
    assert checker.check({"platforms": {"darwin-x86_64": "oops"}}, conf(pubkey))[0] == "darwin-x86_64: no signature"


@pytest.mark.parametrize(
    "garbage",
    ["not base64 !!", b64("just text"), b64("untrusted comment: x\n" + b64(b"short")), b64("untrusted comment: x\n" + b64(b"XX" + bytes(70)))],
)
def test_unreadable_signature_is_reported(garbage: str):
    problems = checker.check(latest(garbage, ["darwin-x86_64"]), conf(THROWAWAY_PUBKEY_A), ("darwin-x86_64",))
    assert len(problems) == 1 and problems[0].startswith("darwin-x86_64: signature unreadable")


def test_unusable_pubkey_is_reported():
    assert checker.check(latest(THROWAWAY_SIGNATURE_A), {})[0].startswith("tauri.conf.json plugins.updater.pubkey: unusable")
    assert checker.check(latest(THROWAWAY_SIGNATURE_A), conf("%%%"))[0].startswith("tauri.conf.json plugins.updater.pubkey: unusable")


def test_committed_public_key_is_readable():
    committed = json.loads((REPO_ROOT / "frontend/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert len(checker.minisign_key_id(committed["plugins"]["updater"]["pubkey"])) == 16


def test_cli_exit_codes(tmp_path: Path, capsys):
    conf_path = tmp_path / "tauri.conf.json"
    conf_path.write_text(json.dumps(conf(THROWAWAY_PUBKEY_A)), encoding="utf-8")
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(latest(THROWAWAY_SIGNATURE_A)), encoding="utf-8")
    bad.write_text(json.dumps(latest(THROWAWAY_SIGNATURE_B)), encoding="utf-8")
    assert checker.main([str(good), str(conf_path)]) == 0
    assert checker.main([str(bad), str(conf_path)]) == 1
    assert "signed with key id" in capsys.readouterr().err
    assert checker.main([str(tmp_path / "missing.json"), str(conf_path)]) == 1
    assert checker.main([]) == 2
