# Manual QA Checklist - chat_v2

## Setup
1. Set `ANALOG_LAYOUT_CHAT_MODE=chat_v2`.
2. Start the GUI.
3. Load a layout.
4. Run initial placement.

## Tests
1. User: `MM3 and MM0 common centroid or interdigitation?`
Expected: Direct answer or synthesized strategy answer. No `I'll delegate...` message.

2. User: `Move MM1 to the left`
Expected: Command proposed and validated. Visual review appears only if command is valid.

3. User: `move left`
Expected: Clarify asks which device. No human viewer.
Then user: `Target device is MM1`
Expected: Move command generated.

4. User: `Check DRC`
Expected: Read-only DRC summary. No visual review.

5. User: `Fix DRC`
Expected: DRC fix commands only if found. Validator runs before visual review.

6. User: `Reduce parasitics`
Expected: Clarify asks for target nets/devices and gives examples.

7. User: `Reduce parasitics on VOUTP and VOUTN`
Expected: Routing optimization analysis/recommendations. No automatic layout changes.

8. User: `align M1 with M2`
Expected: Unsupported/clarify message. No command.

9. User: `add dummy`
Expected: Clarify asks where to add dummy.

## Acceptance Criteria
- `layout_session_app` exists and is selectable with `mode=\"chat_v2\"`.
- `session_chat_app` still works with `mode=\"chat\"`.
- First receiver in chat_v2 is `node_layout_session_agent`.
- AI agent can:
  - answer directly
  - call deterministic parser
  - call slot filling
  - call target-net extraction
  - call topology/strategy/placement specialists
  - call DRC checker/critic
  - call routing previewer
- Validator still enforces supported actions, device existence, row legality, finger integrity, matching warnings, and no empty commands to human viewer.
- User never sees delegation placeholders.
- Specialist outputs are synthesized into final answers.
- Layout-changing commands always pass through `command_validator` before `human_viewer`.
- Read-only checks do not mutate layout.
- New tests pass without real LLM/API/GUI/KLayout.
