# Scene Sequencer

Scene Sequencer is a Home Assistant custom integration that sequences a configured list of `on_scenes` and activates an explicit `off_scene` after a timeout.

## Behavior

- Each config entry owns one independent sequencer state machine.
- The service call only needs the config `entry_id`.
- External `scene.turn_on` calls are observed and update every matching entry.
- The same scene may appear in multiple entries. Each entry updates independently.

## Config

Create the integration through the Home Assistant UI config flow.

Each entry stores:

- `name`
- `on_scenes`
- `off_scene`
- `timeout`
- `transition`

## Service

### `scene_sequencer.cycle`

Call the service with:

- `entry_id`: The Home Assistant config entry ID for the sequencer you want to advance.

## Sequencing Rules

- If the current scene is an `on_scene` and the timeout has not elapsed, the next `on_scene` is activated.
- If the timeout has elapsed, the `off_scene` is activated instead.
- If the current scene is the `off_scene`, the next call returns to the first `on_scene`.
