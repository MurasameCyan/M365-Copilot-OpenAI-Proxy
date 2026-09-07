from __future__ import annotations

import json

from fastapi.testclient import TestClient

from m365_copilot_openai_proxy.app import create_app
from m365_copilot_openai_proxy.config import Settings
from m365_copilot_openai_proxy import runtime_settings
from m365_copilot_openai_proxy.runtime_settings import normalize_tone_options
from m365_copilot_openai_proxy.tone_options import TONE_OPTIONS, TONE_VALUES


EXPECTED_TONE_VALUES = {
    "Magic",
    "Chat",
    "Reasoning",
    "Claude_Sonnet",
    "Claude_Sonnet_Reasoning",
    "Claude_Fable",
    "Claude_Opus",
    "Gpt_6_Astra",
    "Gpt_6_Reasoning",
    "Gpt_5_6_Chat",
    "Gpt_5_6_Reasoning",
    "Gpt_5_5_Chat",
    "Gpt_5_5_Reasoning",
    "Gpt_5_4_Chat",
    "Gpt_5_4_Reasoning",
    "Gpt_5_3_Chat",
    "Gpt_5_3_Reasoning",
    "Gpt_5_2_Chat",
    "Gpt_5_2_Reasoning",
}

EXPECTED_TONE_OPTIONS = [
    ("Magic", "Copilot_自动"),
    ("Chat", "Copilot_快速答复"),
    ("Reasoning", "Copilot_深度思考"),
    ("Claude_Sonnet", "claude-sonnet-4-6"),
    ("Claude_Sonnet_Reasoning", "claude-sonnet-4-5"),
    ("Claude_Fable", "claude-fable-5"),
    ("Claude_Opus", "claude-opus"),
    ("Gpt_6_Astra", "gpt-6_Chat"),
    ("Gpt_6_Reasoning", "gpt-6"),
    ("Gpt_5_6_Chat", "gpt-5.6_Chat"),
    ("Gpt_5_6_Reasoning", "gpt-5.6"),
    ("Gpt_5_5_Chat", "gpt-5.5_Chat"),
    ("Gpt_5_5_Reasoning", "gpt-5.5"),
    ("Gpt_5_4_Chat", "gpt-5.4_Chat"),
    ("Gpt_5_4_Reasoning", "gpt-5.4"),
    ("Gpt_5_3_Chat", "gpt-5.3_Chat"),
    ("Gpt_5_3_Reasoning", "gpt-5.3"),
    ("Gpt_5_2_Chat", "gpt-5.2_Chat"),
    ("Gpt_5_2_Reasoning", "gpt-5.2"),
]

PREVIOUS_DEFAULT_LABELS = {
    "Claude_Sonnet_Reasoning": "claude-sonnet-4-5_Reasoning",
    "Gpt_5_6_Reasoning": "gpt-5.6_Reasoning",
    "Gpt_5_5_Reasoning": "gpt-5.5_Reasoning",
    "Gpt_5_4_Reasoning": "gpt-5.4_Reasoning",
    "Gpt_5_2_Reasoning": "gpt-5.2_Reasoning",
}

# The historical on-disk default, pinned by value+order. This must NOT be
# derived from the live TONE_OPTIONS: the migration in
# runtime_settings fires only on an *exact* match against the bytes an older
# release persisted, so adding a tone to the current catalogue must leave this
# list untouched. Rebuilding it here (rather than importing
# _PREVIOUS_BUILTIN_TONE_OPTIONS) keeps the double-entry check that would catch
# a typo in that production constant.
PREVIOUS_DEFAULT_VALUES = [
    "Magic",
    "Chat",
    "Reasoning",
    "Claude_Sonnet",
    "Claude_Sonnet_Reasoning",
    "Claude_Fable",
    "Claude_Opus",
    "Gpt_5_6_Reasoning",
    "Gpt_5_5_Chat",
    "Gpt_5_5_Reasoning",
    "Gpt_5_4_Chat",
    "Gpt_5_4_Reasoning",
    "Gpt_5_3_Chat",
    "Gpt_5_2_Chat",
    "Gpt_5_2_Reasoning",
]


def _previous_default_tone_options():
    by_value = {option["value"]: option for option in TONE_OPTIONS}
    options = []
    for value in PREVIOUS_DEFAULT_VALUES:
        old = dict(by_value[value])
        label = PREVIOUS_DEFAULT_LABELS.get(value, old["label"])
        old.update(label=label, label_zh=label, label_en=label)
        options.append(old)
    return options


def test_tone_options_define_supported_modes():
    assert {option["value"] for option in TONE_OPTIONS} == EXPECTED_TONE_VALUES
    assert TONE_VALUES == EXPECTED_TONE_VALUES
    assert [(option["value"], option["label"]) for option in TONE_OPTIONS] == EXPECTED_TONE_OPTIONS
    assert all(option["label_zh"] == option["label"] for option in TONE_OPTIONS)
    assert all(option["label_en"] == option["label"] for option in TONE_OPTIONS)
    assert all({"value", "label", "label_zh", "label_en"} <= set(option) for option in TONE_OPTIONS)


def test_create_app_exposes_shared_tone_options(tmp_path):
    client = TestClient(create_app(Settings(TOKEN_DIR=str(tmp_path), API_KEY="", ADMIN_PASSWORD="")))

    response = client.get("/admin/tone")

    assert response.status_code == 200
    options = response.json()["options"]
    # Tone options are now admin-editable (persisted in runtime settings); with no
    # override the picker defaults to the built-in modes, passed through
    # normalize_tone_options (2-column format: display names have whitespace
    # collapsed to underscores and label_en mirrors the display name). Compare
    # against that normalized contract rather than the raw built-in list.
    expected = normalize_tone_options([dict(o) for o in TONE_OPTIONS])
    assert [(o["value"], o["label_zh"], o["label_en"]) for o in options] == [
        (o["value"], o["label_zh"], o["label_en"]) for o in expected
    ]


def test_read_runtime_settings_migrates_exact_previous_m365_default(tmp_path):
    (tmp_path / "runtime_settings.json").write_text(
        json.dumps({"tone_options": _previous_default_tone_options()}),
        encoding="utf-8",
    )

    settings = runtime_settings._read_runtime_settings(str(tmp_path))

    assert [(option["value"], option["label"]) for option in settings["tone_options"]] == EXPECTED_TONE_OPTIONS


def test_read_runtime_settings_preserves_reordered_previous_m365_default(tmp_path):
    custom_options = _previous_default_tone_options()
    custom_options[0], custom_options[1] = custom_options[1], custom_options[0]
    (tmp_path / "runtime_settings.json").write_text(
        json.dumps({"tone_options": custom_options}),
        encoding="utf-8",
    )

    settings = runtime_settings._read_runtime_settings(str(tmp_path))

    assert settings["tone_options"] == custom_options


# The defaults shipped between the label rename and today, pinned by value+order
# for the same double-entry reason as PREVIOUS_DEFAULT_VALUES: the production
# constant derives these from TONE_OPTIONS, so a test that derived them the same
# way would pass even if both were wrong together. Labels are the current ones --
# only the tone set differs from today's default.
EXPECTED_TONE_VALUE_ORDER = [value for value, _label in EXPECTED_TONE_OPTIONS]
DEFAULT_VALUES_BEFORE_GPT_6_ASTRA = [
    "Magic",
    "Chat",
    "Reasoning",
    "Claude_Sonnet",
    "Claude_Sonnet_Reasoning",
    "Claude_Fable",
    "Claude_Opus",
    "Gpt_5_6_Chat",
    "Gpt_5_6_Reasoning",
    "Gpt_5_5_Chat",
    "Gpt_5_5_Reasoning",
    "Gpt_5_4_Chat",
    "Gpt_5_4_Reasoning",
    "Gpt_5_3_Chat",
    "Gpt_5_3_Reasoning",
    "Gpt_5_2_Chat",
    "Gpt_5_2_Reasoning",
]
DEFAULT_VALUES_BEFORE_GPT_6_REASONING = [
    *DEFAULT_VALUES_BEFORE_GPT_6_ASTRA[:7],
    "Gpt_6_Astra",
    *DEFAULT_VALUES_BEFORE_GPT_6_ASTRA[7:],
]
DEFAULT_VALUES_BEFORE_GPT_5_6_CHAT = [
    v for v in DEFAULT_VALUES_BEFORE_GPT_6_ASTRA if v != "Gpt_5_6_Chat"
]
DEFAULT_VALUES_BEFORE_GPT_5_3_REASONING = [
    v for v in DEFAULT_VALUES_BEFORE_GPT_5_6_CHAT if v != "Gpt_5_3_Reasoning"
]


def _default_tone_options_limited_to(values):
    by_value = {option["value"]: option for option in TONE_OPTIONS}
    return [dict(by_value[value]) for value in values]


def test_read_runtime_settings_migrates_defaults_that_predate_each_added_tone(tmp_path):
    # Production sat on the second of these for two releases: the rename
    # migration had already rewritten its labels, so it matched neither the
    # old-label literal nor the current default, and every tone added afterwards
    # was locked out of the picker until someone wrote the list by hand.
    for pinned in (
        DEFAULT_VALUES_BEFORE_GPT_6_REASONING,
        DEFAULT_VALUES_BEFORE_GPT_6_ASTRA,
        DEFAULT_VALUES_BEFORE_GPT_5_6_CHAT,
        DEFAULT_VALUES_BEFORE_GPT_5_3_REASONING,
    ):
        (tmp_path / "runtime_settings.json").write_text(
            json.dumps({"tone_options": _default_tone_options_limited_to(pinned)}),
            encoding="utf-8",
        )

        settings = runtime_settings._read_runtime_settings(str(tmp_path))

        assert [
            (option["value"], option["label"]) for option in settings["tone_options"]
        ] == EXPECTED_TONE_OPTIONS, pinned


def test_read_runtime_settings_keeps_a_list_an_operator_actually_edited(tmp_path):
    # The guard on widening the migration: "default minus a tone" must only be
    # treated as untouched when that tone postdates the persisted list. A tone
    # the operator deliberately removed has to stay removed, or the upgrade
    # silently hands their users back a model they withdrew.
    custom_options = _default_tone_options_limited_to(
        [v for v in EXPECTED_TONE_VALUE_ORDER if v != "Claude_Opus"]
    )
    (tmp_path / "runtime_settings.json").write_text(
        json.dumps({"tone_options": custom_options}),
        encoding="utf-8",
    )

    settings = runtime_settings._read_runtime_settings(str(tmp_path))

    assert settings["tone_options"] == custom_options
    assert "Claude_Opus" not in [o["value"] for o in settings["tone_options"]]
