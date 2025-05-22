import json
import hashlib
import time
import logging
from homeassistant.core import HomeAssistant, ServiceCall

DOMAIN = "scene_sequencer"
STORE_SENSOR = "binary_sensor.scene_sequencer_store"
JSON_ATTR = "data"
TIMEOUT_PROPERTY = "go_to_last_timeout"

JSON_INDEX_PROPERTY = "idx"
JSON_TIMESTAMP_PROPERTY = "ts"
JSON_LAST_USED_PROPERTY = "last_used"
CLEANUP_AGE_SECONDS = 10 * 24 * 60 * 60  # 10 days in seconds

LOGGER = logging.getLogger(__name__)

def generate_key(scenes: list[str]) -> str:
    """
    Generate a unique, deterministic identifier for a sequence of scenes.

    Creates a shortened MD5 hash from the scene entity IDs, ensuring each unique 
    scene sequence has its own persistent tracking data.

    Args:
        scenes: List of scene entity IDs

    Returns:
        10-character hash string that uniquely identifies this scene sequence
    """
    return hashlib.md5(",".join(scenes).encode()).hexdigest()[:10]

async def is_current_state_matching_scene(hass: HomeAssistant, scene: str) -> bool:
    """
    Check if for a given scene, the current state of entities matches the state defined by the scene.

    Args:
        hass: Home Assistant instance
        scene: Scene entity ID

    Returns:
        True if the current state of all entities matches the scene's state, False otherwise
    """
    # Get the scene state object
    scene_state = hass.states.get(scene)
    if not scene_state or not scene_state.attributes.get("entity_id"):
        LOGGER.warning("Scene %s not found or has no entity_id attribute", scene)
        return False  # Assume unmatch to avoid errors

    # Get entities and their states from the scene
    scene_entities = scene_state.attributes.get("entity_id", [])
    for entity in scene_entities:
        # Get the current state of the entity
        current_state = hass.states.get(entity)
        if not current_state:
            LOGGER.warning("Entity %s has no state", entity)
            return False

        # Get the expected state from the scene's attributes
        scene_attributes = scene_state.attributes.get("attributes", {}).get(entity, {})
        expected_state = scene_attributes.get("state", "off")  # Default to "off" for lights

        # Compare current state with expected state
        if current_state.state != expected_state:
            return False

    return True

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """
    Set up the Scene Sequencer component.

    Registers the cycle service that allows sequencing through scenes.

    Args:
        hass: Home Assistant instance
        config: Component configuration

    Returns:
        True indicating successful setup
    """

    async def handle_sequence(call: ServiceCall):
        """
        Handle the scene_sequencer.cycle service call.

        Advances through a sequence of scenes, tracking position for each unique
        sequence. Supports jumping to the final scene after a period of inactivity.
        If the current state matches the last scene during timeout, activates the first scene.

        Args:
            call: Service call data containing scenes and optional timeout
        """
        # Extract scenes from service call data, supporting both list and dict formats
        raw = call.data.get("scenes", [])
        if isinstance(raw, dict) and "entity_id" in raw:
            scenes = raw["entity_id"]
        elif isinstance(raw, list):
            scenes = raw
        else:
            scenes = []

        # Exit if no scenes provided
        if not scenes:
            return

        # Get timeout parameter (seconds) for jumping to last scene
        go_to_last_timeout = call.data.get(TIMEOUT_PROPERTY)

        # Create unique identifier for this scene sequence
        key = generate_key(scenes)

        # Retrieve persistent storage data for all sequences
        state = hass.states.get(STORE_SENSOR)
        try:
            data = state.attributes.get(JSON_ATTR, "{}") if state else "{}"
            mapping = json.loads(data)
        except Exception:
            mapping = {}

        # Clean up old entries (older than 'CLEANUP_AGE_SECONDS')
        current_time = time.time()
        keys_to_remove = [
            k for k, v in mapping.items()
            if (JSON_LAST_USED_PROPERTY in v and
                (current_time - v[JSON_LAST_USED_PROPERTY ]) > CLEANUP_AGE_SECONDS)
        ]
        for k in keys_to_remove:
            del mapping[k]

        # Get stored sequence state or initialize defaults
        entry = mapping.get(key, {JSON_INDEX_PROPERTY: 0, JSON_TIMESTAMP_PROPERTY: 0})
        idx = entry.get(JSON_INDEX_PROPERTY, 0)  # Current position in sequence
        last_ts = entry.get(JSON_TIMESTAMP_PROPERTY, 0)  # Timestamp of last activation

        # Calculate current timestamp and target scene
        new_idx = (idx + 1) % len(scenes)
        now_ts = time.time()
        target_scene = scenes[idx % len(scenes)]

        # Check if we should jump to the last scene due to timeout
        if go_to_last_timeout and last_ts > 0 and (now_ts - last_ts) >= go_to_last_timeout:
            # Check if current state matches the last scene
            if await is_current_state_matching_scene(hass, scenes[-1]):
                target_scene = scenes[0]
                new_idx = 1
            else:
                target_scene = scenes[-1]
                new_idx = 0  # Reset to beginning for next activation

        # If last scene has been activated, force never trigger timeout
        if new_idx == 0:
            now_ts = 0

        # Activate the target scene
        await hass.services.async_call(
            "scene", "turn_on",
            {"entity_id": target_scene},
            blocking=True
        )

        # Update stored state for this sequence
        mapping[key] = {
            JSON_INDEX_PROPERTY: new_idx,
            JSON_TIMESTAMP_PROPERTY: now_ts,
            JSON_LAST_USED_PROPERTY: time.time()
        }

        # Save state to persistent storage
        hass.states.async_set(STORE_SENSOR, True, {JSON_ATTR: json.dumps(mapping)})

    # Register the component's service
    hass.services.async_register(DOMAIN, "cycle", handle_sequence)
    return True