from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, Event, HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store

from .const import (
    CONF_NAME,
    CONF_OFF_SCENE,
    CONF_ON_SCENES,
    CONF_TIMEOUT,
    CONF_TRANSITION,
    DOMAIN,
    SERVICE_CYCLE,
    SERVICE_SCENE_OFF,
    SERVICE_SCENE_ON,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SequencerConfig:
    name: str
    on_scenes: list[str]
    off_scene: str | None
    timeout: int | None
    transition: int


@dataclass(slots=True)
class SequencerState:
    current_scene: str | None = None
    last_activated_at: float = 0.0


@dataclass(slots=True)
class SequencerManager:
    hass: HomeAssistant
    store: Store[dict[str, dict[str, Any]]] = field(init=False)
    configs: dict[str, SequencerConfig] = field(default_factory=dict)
    states: dict[str, SequencerState] = field(default_factory=dict)
    scene_index: dict[str, set[str]] = field(default_factory=dict)
    _internal_scene_context_ids: set[str] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _unsub_call_service: Any = None

    def __post_init__(self) -> None:
        self.store = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if not stored:
            return

        entries = stored.get("entries", {}) if isinstance(stored, dict) else {}
        for entry_id, payload in entries.items():
            if not isinstance(payload, dict):
                continue
            self.states[entry_id] = SequencerState(
                current_scene=payload.get("current_scene"),
                last_activated_at=float(payload.get("last_activated_at", 0.0)),
            )

    async def async_add_entry(self, entry_id: str, config: dict[str, Any]) -> None:
        await self._async_store_entry(entry_id, config, log_action="added")

    async def async_update_entry(self, entry_id: str, config: dict[str, Any]) -> None:
        await self._async_store_entry(entry_id, config, log_action="updated")

    async def _async_store_entry(self, entry_id: str, config: dict[str, Any], log_action: str) -> None:
        off_scene_raw = config.get(CONF_OFF_SCENE)
        timeout_raw = config.get(CONF_TIMEOUT)
        sequencer_config = SequencerConfig(
            name=str(config[CONF_NAME]),
            on_scenes=list(config[CONF_ON_SCENES]),
            off_scene=str(off_scene_raw) if off_scene_raw else None,
            timeout=int(timeout_raw) if timeout_raw not in (None, "") else None,
            transition=int(config.get(CONF_TRANSITION, 0)),
        )

        async with self._lock:
            previous_config = self.configs.get(entry_id)
            if previous_config is not None:
                previous_scenes = list(previous_config.on_scenes)
                if previous_config.off_scene is not None:
                    previous_scenes.append(previous_config.off_scene)

                for scene_id in previous_scenes:
                    entry_ids = self.scene_index.get(scene_id)
                    if entry_ids is None:
                        continue
                    entry_ids.discard(entry_id)
                    if not entry_ids:
                        self.scene_index.pop(scene_id, None)

            self.configs[entry_id] = sequencer_config
            self.states.setdefault(entry_id, SequencerState())
            entry_scenes = list(sequencer_config.on_scenes)
            if sequencer_config.off_scene is not None:
                entry_scenes.append(sequencer_config.off_scene)

            for scene_id in entry_scenes:
                self.scene_index.setdefault(scene_id, set()).add(entry_id)
            await self._async_save()
            _LOGGER.debug(
                "Scene Sequencer entry %s: %s (name=%s, on_scenes=%s, off_scene=%s, timeout=%s, transition=%s)",
                entry_id,
                log_action,
                sequencer_config.name,
                sequencer_config.on_scenes,
                sequencer_config.off_scene,
                sequencer_config.timeout,
                sequencer_config.transition,
            )

    async def async_remove_entry(self, entry_id: str) -> None:
        async with self._lock:
            config = self.configs.pop(entry_id, None)
            self.states.pop(entry_id, None)
            if config is not None:
                entry_scenes = list(config.on_scenes)
                if config.off_scene is not None:
                    entry_scenes.append(config.off_scene)

                for scene_id in entry_scenes:
                    entry_ids = self.scene_index.get(scene_id)
                    if entry_ids is None:
                        continue
                    entry_ids.discard(entry_id)
                    if not entry_ids:
                        self.scene_index.pop(scene_id, None)
            await self._async_save()
            _LOGGER.debug("Scene Sequencer entry removed: %s", entry_id)

    async def async_handle_service_call(self, call: ServiceCall) -> None:
        entry_id = self._resolve_service_target_entry_id(call)
        if not entry_id:
            return

        async with self._lock:
            config = self.configs.get(entry_id)
            if config is None:
                _LOGGER.warning("Service call for unknown entry_id: %s", entry_id)
                return

            state = self.states.setdefault(entry_id, SequencerState())
            target_scene = self._resolve_target_scene(config, state)
            if target_scene is None:
                _LOGGER.warning("Could not resolve target scene for entry_id=%s. Check on_scenes configuration.", entry_id)
                return

        await self._async_activate_scene(
            entry_id=entry_id,
            entry_name=config.name,
            target_scene=target_scene,
            transition=config.transition,
            parent_context_id=call.context.id,
            source_service=SERVICE_CYCLE,
        )

    async def async_handle_scene_on_call(self, call: ServiceCall) -> None:
        entry_id = self._resolve_service_target_entry_id(call)
        if not entry_id:
            return

        async with self._lock:
            config = self.configs.get(entry_id)
            if config is None:
                _LOGGER.warning("scene_on call for unknown entry_id: %s", entry_id)
                return

            state = self.states.setdefault(entry_id, SequencerState())
            if state.current_scene in config.on_scenes:
                _LOGGER.debug(
                    "scene_on no-op for entry %s(%s): already on scene=%s",
                    entry_id,
                    config.name,
                    state.current_scene,
                )
                return

            if not config.on_scenes:
                _LOGGER.warning("scene_on could not resolve target scene for entry_id=%s. Check on_scenes configuration.", entry_id)
                return

            target_scene = config.on_scenes[0]

        await self._async_activate_scene(
            entry_id=entry_id,
            entry_name=config.name,
            target_scene=target_scene,
            transition=config.transition,
            parent_context_id=call.context.id,
            source_service=SERVICE_SCENE_ON,
        )

    async def async_handle_scene_off_call(self, call: ServiceCall) -> None:
        entry_id = self._resolve_service_target_entry_id(call)
        if not entry_id:
            return

        async with self._lock:
            config = self.configs.get(entry_id)
            if config is None:
                _LOGGER.warning("scene_off call for unknown entry_id: %s", entry_id)
                return

            if not config.off_scene:
                _LOGGER.warning("scene_off called for entry_id=%s but no off_scene is configured", entry_id)
                return

            target_scene = config.off_scene

        await self._async_activate_scene(
            entry_id=entry_id,
            entry_name=config.name,
            target_scene=target_scene,
            transition=config.transition,
            parent_context_id=call.context.id,
            source_service=SERVICE_SCENE_OFF,
        )

    async def _async_activate_scene(
        self,
        entry_id: str,
        entry_name: str,
        target_scene: str,
        transition: int,
        parent_context_id: str | None,
        source_service: str,
    ) -> None:
        # Apply the activation to all entries tracking this scene so shared-scene
        # sequences stay in sync for internal service calls.
        async with self._lock:
            now = time.time()
            updated_entries = 0
            for related_entry_id in self.scene_index.get(target_scene, set()):
                related_state = self.states.setdefault(related_entry_id, SequencerState())
                related_state.current_scene = target_scene
                related_state.last_activated_at = now
                updated_entries += 1

            await self._async_save()
            _LOGGER.debug(
                "%s handled for entry %s(%s): target_scene=%s, updated_entries=%d",
                source_service,
                entry_id,
                entry_name,
                target_scene,
                updated_entries,
            )

        scene_context = Context(parent_id=parent_context_id)
        self._internal_scene_context_ids.add(str(scene_context.id))

        try:
            await self.hass.services.async_call(
                "scene",
                "turn_on",
                {"entity_id": target_scene, "transition": transition},
                blocking=True,
                context=scene_context,
            )
        finally:
            self._internal_scene_context_ids.discard(str(scene_context.id))

        _LOGGER.debug(
            "Scene activated by %s for entry %s: scene=%s, transition=%s seconds",
            source_service,
            entry_id,
            target_scene,
            transition,
        )

    def _resolve_service_target_entry_id(self, call: ServiceCall) -> str | None:
        entry_id = str(call.data.get("entry_id", "")).strip()
        name = str(call.data.get("name", "")).strip()

        if entry_id:
            return entry_id

        if not name:
            _LOGGER.warning("Service call received without entry_id or name")
            return None

        matching_entry_ids = [
            configured_entry_id
            for configured_entry_id, config in self.configs.items()
            if config.name == name
        ]

        if not matching_entry_ids:
            _LOGGER.warning("Service call for unknown entry name: %s", name)
            return None

        if len(matching_entry_ids) > 1:
            _LOGGER.warning(
                "Service call entry name is ambiguous: %s (matches=%s)",
                name,
                matching_entry_ids,
            )
            return None

        return matching_entry_ids[0]

    async def async_handle_scene_service_event(self, event: Event) -> None:
        if event.data.get("domain") != "scene" or event.data.get("service") != "turn_on":
            return

        context_id = event.context.id
        if context_id is not None and str(context_id) in self._internal_scene_context_ids:
            self._internal_scene_context_ids.discard(str(context_id))
            _LOGGER.debug(
                "Ignored internal scene activation event: context_id=%s",
                context_id,
            )
            return

        service_data = event.data.get("service_data", {})
        scene_ids = self._normalize_entity_ids(service_data.get("entity_id"))
        if not scene_ids:
            _LOGGER.debug("Scene service event received but no scene_id found")
            return

        async with self._lock:
            changed = False
            now = time.time()
            for scene_id in scene_ids:
                entry_ids = self.scene_index.get(scene_id)
                if not entry_ids:
                    _LOGGER.debug("Scene activated externally but no entry tracking it: %s", scene_id)
                    continue

                for entry_id in entry_ids:
                    state = self.states.setdefault(entry_id, SequencerState())
                    state.current_scene = scene_id
                    state.last_activated_at = now
                    changed = True
                    _LOGGER.debug(
                        "External scene activation detected for entry %s: scene=%s",
                        entry_id,
                        scene_id,
                    )

            if changed:
                await self._async_save()

    def _resolve_target_scene(self, config: SequencerConfig, state: SequencerState) -> str | None:
        if not config.on_scenes:
            return None

        if config.off_scene and state.current_scene == config.off_scene:
            return config.on_scenes[0]

        if state.current_scene in config.on_scenes:
            if (
                config.off_scene
                and config.timeout is not None
                and state.last_activated_at > 0
                and (time.time() - state.last_activated_at) >= config.timeout
            ):
                return config.off_scene

            current_index = config.on_scenes.index(state.current_scene)
            if config.off_scene:
                # If at the last on_scene, transition to off_scene; otherwise move to next on_scene
                if current_index == len(config.on_scenes) - 1:
                    return config.off_scene
                return config.on_scenes[current_index + 1]

            # No off_scene configured: always cycle through on_scenes.
            return config.on_scenes[(current_index + 1) % len(config.on_scenes)]

        return config.on_scenes[0]

    async def _async_save(self) -> None:
        data = {
            "entries": {
                entry_id: {
                    "current_scene": state.current_scene,
                    "last_activated_at": state.last_activated_at,
                }
                for entry_id, state in self.states.items()
            }
        }
        await self.store.async_save(data)

    @staticmethod
    def _normalize_entity_ids(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if item]
        return []


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    del config

    _LOGGER.debug("Setting up Scene Sequencer integration")
    manager = SequencerManager(hass)
    await manager.async_load()
    _LOGGER.debug("Scene Sequencer state loaded: %d entries", len(manager.configs))

    hass.data.setdefault(DOMAIN, {})["manager"] = manager

    hass.services.async_register(DOMAIN, SERVICE_CYCLE, manager.async_handle_service_call)
    hass.services.async_register(DOMAIN, SERVICE_SCENE_ON, manager.async_handle_scene_on_call)
    hass.services.async_register(DOMAIN, SERVICE_SCENE_OFF, manager.async_handle_scene_off_call)
    _LOGGER.debug("Scene Sequencer service registered")
    manager._unsub_call_service = hass.bus.async_listen(EVENT_CALL_SERVICE, manager.async_handle_scene_service_event)
    _LOGGER.debug("Scene Sequencer event listener registered")
    return True


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    _LOGGER.debug("Setting up Scene Sequencer entry: %s", entry.entry_id)
    manager: SequencerManager = hass.data[DOMAIN]["manager"]
    config = dict(entry.data)
    config.update(entry.options)
    await manager.async_add_entry(entry.entry_id, config)
    entry.async_on_unload(entry.add_update_listener(_async_entry_update_listener))
    return True


async def _async_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _LOGGER.debug("Updating Scene Sequencer entry from options flow: %s", entry.entry_id)
    manager: SequencerManager = hass.data[DOMAIN]["manager"]
    config = dict(entry.data)
    config.update(entry.options)
    await manager.async_update_entry(entry.entry_id, config)
    entry_title = str(config[CONF_NAME])
    if entry.title != entry_title:
        await hass.config_entries.async_update_entry(entry, title=entry_title)


async def async_unload_entry(hass: HomeAssistant, entry) -> bool:
    _LOGGER.debug("Unloading Scene Sequencer entry: %s", entry.entry_id)
    manager: SequencerManager = hass.data[DOMAIN]["manager"]
    await manager.async_remove_entry(entry.entry_id)
    return True
