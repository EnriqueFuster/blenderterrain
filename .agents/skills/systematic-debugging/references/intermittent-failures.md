# Intermittent failures

Adapted from Superpowers' condition-based-waiting guidance.

- Record when the error occurs, its approximate frequency and relevant state.
- Separate deterministic parsing/geometry failures from network or timing failures.
- Wait for an observable condition with a timeout instead of adding arbitrary sleeps.
- Make retries bounded and appropriate to an operation's failure mode.
- Keep deterministic tests local; reserve live-provider checks for explicit smoke tests.
- Do not interpret one successful execution as proof that an intermittent bug is fixed.
