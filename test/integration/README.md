# Queue API integration tests

`test_queue_api.py` drives the bridge's mission queue API through `IROCClient`, against the docker
compose stack of the ground repo (`../compose`, override with `HOLOSWARM_COMPOSE_DIR`). The tests
start the stack with `compose/up.sh` and stop it with `compose/down.sh`; `HOLOSWARM_TEST_STACK`
selects the environment:

| `HOLOSWARM_TEST_STACK` | Stack | Covers | Time |
| --- | --- | --- | --- |
| `ground` | `FAKE_ROBOTS="uav1 uav2" ./up.sh ground-only`: zenoh, fleet manager, bridge, fake mission handlers | everything; fake robots "fly" a started goal for 2 s and refuse uploads of tasks with `refuse-upload` in the id | ~1 min |
| `sim` | `SIM_UAVS=uav1 SIM_TAKEOFF=0 ./up.sh simulation`: one simulated drone that stays on the ground | staging on the real mission handler; tests that need a flying drone or fake robots are skipped | ~2 min |
| `flight` | `SIM_UAVS=uav1 ./up.sh simulation`: one simulated drone that takes off | everything except the fake-robot refusal, with real flights | ~6 min |

```bash
cd holoswarm-client
HOLOSWARM_TEST_STACK=ground .venv/bin/python -m unittest discover -s test/integration -v
```

- Without `HOLOSWARM_TEST_STACK` the tests are skipped.
- If any compose stack is already running (`ground`, `sim` or `uav*` containers) the tests are skipped
  and the stack is left alone: it may be a live experiment. Stop it with `compose/down.sh` first.
- `HOLOSWARM_TEST_KEEP=1` leaves the stack running afterwards, e.g. to read the logs in
  `compose/_logs/<newest>/`. Stop it with `compose/down.sh`.
- The containers run `holoswarm_ros_packages/install`; rebuild it with `compose/build.sh` (stack down)
  after changing ROS code.
- The fleet manager outage tests stop and restart the `ground-fleet_manager-1` container; the persistence test
  restarts `ground-iroc_bridge-1`.
- The bridge stores queues in a test database of its own (`BRIDGE_CUSTOM_CONFIG=./compose/testing/bridge_test.yaml`
  → `ROOT/.assets/test.sqlite`, reset at every run), never in the operator's `ROOT/.assets/holoswarm.sqlite`.
