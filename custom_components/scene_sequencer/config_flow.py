from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.core import callback

from .const import (
    CONF_NAME,
    CONF_OFF_SCENE,
    CONF_ON_SCENES,
    CONF_TIMEOUT,
    CONF_TRANSITION,
    DEFAULT_TIMEOUT,
    DOMAIN,
)


def _base_schema(defaults: dict[str, object] | None = None) -> vol.Schema:
    defaults = defaults or {}
    schema: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): selector.TextSelector(),
        vol.Required(
            CONF_ON_SCENES,
            default=defaults.get(CONF_ON_SCENES, []),
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="scene", multiple=True)
        ),
    }

    off_scene_default = defaults.get(CONF_OFF_SCENE)
    if off_scene_default:
        schema[
            vol.Optional(
                CONF_OFF_SCENE,
                default=off_scene_default,
            )
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="scene"))
    else:
        schema[vol.Optional(CONF_OFF_SCENE)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="scene")
        )

    timeout_default = defaults.get(CONF_TIMEOUT)
    if timeout_default is None:
        timeout_default = DEFAULT_TIMEOUT if off_scene_default else None

    timeout_selector = selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=86400, step=1, mode=selector.NumberSelectorMode.BOX)
    )
    if timeout_default is None:
        schema[vol.Optional(CONF_TIMEOUT)] = timeout_selector
    else:
        schema[vol.Optional(CONF_TIMEOUT, default=timeout_default)] = timeout_selector

    schema[vol.Optional(CONF_TRANSITION, default=defaults.get(CONF_TRANSITION, 0))] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=300, step=1, mode=selector.NumberSelectorMode.BOX)
    )

    return vol.Schema(schema)


def _validate_entry_data(user_input: dict[str, object]) -> dict[str, object]:
    name = str(user_input[CONF_NAME]).strip()
    if not name:
        raise ValueError("Name is required")

    on_scenes = list(user_input[CONF_ON_SCENES])
    if not on_scenes:
        raise ValueError("At least one on scene is required")

    if len(set(on_scenes)) != len(on_scenes):
        raise ValueError("On scenes must be unique")

    off_scene_raw = user_input.get(CONF_OFF_SCENE)
    off_scene = str(off_scene_raw).strip() if off_scene_raw else None
    if off_scene and off_scene in on_scenes:
        raise ValueError("Off scene must not be part of the on scenes list")

    timeout_raw = user_input.get(CONF_TIMEOUT)
    timeout = int(timeout_raw) if timeout_raw not in (None, "") else None
    if off_scene and timeout is None:
        raise ValueError("Timeout is required when off scene is set")
    if timeout is not None and timeout < 1:
        raise ValueError("Timeout must be greater than zero")

    transition = int(user_input.get(CONF_TRANSITION, 0))
    if transition < 0:
        raise ValueError("Transition must be zero or greater")

    return {
        CONF_NAME: name,
        CONF_ON_SCENES: on_scenes,
        CONF_OFF_SCENE: off_scene,
        CONF_TIMEOUT: timeout,
        CONF_TRANSITION: transition,
    }


class SceneSequencerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SceneSequencerOptionsFlowHandler:
        return SceneSequencerOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, object] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                validated = _validate_entry_data(user_input)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                await self.async_set_unique_id(validated[CONF_NAME].strip().lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=validated[CONF_NAME], data=validated)

        return self.async_show_form(step_id="user", data_schema=_base_schema(user_input), errors=errors)

class SceneSequencerOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, object] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        defaults = dict(self._config_entry.data)
        defaults.update(self._config_entry.options)

        if user_input is not None:
            try:
                validated = _validate_entry_data(user_input)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self.async_create_entry(title="", data=validated)

        return self.async_show_form(step_id="init", data_schema=_base_schema(defaults), errors=errors)
