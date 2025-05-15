import json
import hashlib
import time
from homeassistant.core import HomeAssistant, ServiceCall

DOMAIN = "scene_sequencer"
STORE_SENSOR = "binary_sensor.scene_sequencer_store"


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
        
        Args:
            call: Service call data containing scenes and optional timeout
        """
        # Extract scenes from service call data, supporting both list and dict formats
        raw = call.data.get("scenes", [])
        if isinstance(raw, dict) and "entity_id" in raw:
            # Handle entity selector format
            scenes = raw["entity_id"]
        elif isinstance(raw, list):
            # Handle direct list format
            scenes = raw
        else:
            scenes = []

        # Exit if no scenes provided
        if not scenes:
            return

        # Get timeout parameter (seconds) for jumping to last scene
        go_to_last_delay = call.data.get("go_to_last_delay")

        # Create unique identifier for this scene sequence
        key = generate_key(scenes)
        
        # Retrieve persistent storage data for all sequences
        state = hass.states.get(STORE_SENSOR)
        try:
            data = state.attributes.get("data", {}) if state else {}
            mapping = json.loads(data)
        except Exception:
            # Initialize empty storage if retrieval fails
            mapping = {}

        # Get stored sequence state or initialize defaults
        entry = mapping.get(key, {"idx": 0, "ts": 0})
        idx = entry.get("idx", 0)  # Current position in sequence
        last_ts = entry.get("ts", 0)  # Timestamp of last activation

        # Calculate current timestamp and target scene
        new_idx = (idx + 1) % len(scenes)
        now_ts = 0 if new_idx == 0 else time.time()
        target_scene = scenes[idx % len(scenes)]

        # Check if we should jump to the last scene due to timeout
        if go_to_last_delay and last_ts > 0 and (now_ts - last_ts) >= go_to_last_delay:
            target_scene = scenes[-1]
            new_idx = 0  # Reset to beginning for next activation
            now_ts = 0   # Reset timestamp

        # Activate the target scene
        await hass.services.async_call(
            "scene", "turn_on",
            {"entity_id": target_scene},
            blocking=True
        )

        # Update stored state for this sequence
        mapping[key] = {"idx": new_idx, "ts": now_ts}
        
        # Save state to persistent storage
        hass.states.async_set(STORE_SENSOR, True, {"data": json.dumps(mapping)})

    # Register the component's service
    hass.services.async_register(DOMAIN, "cycle", handle_sequence)
    return True