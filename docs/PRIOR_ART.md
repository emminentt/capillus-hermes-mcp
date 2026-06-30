# Prior Art

Capillus publishes mobile apps for Bluetooth-enabled caps. Public app listings describe Bluetooth pairing, a 6-minute daily treatment timer, automatic treatment logging, reminders, calendar views, and progress photos.

There does not appear to be a public Capillus-specific reverse-engineering repo as of June 30, 2026.

Relevant public references:

- App Store: https://apps.apple.com/us/app/capillus/id6737130992
- Google Play: https://play.google.com/store/apps/details?id=com.capillus.cap
- Capillus Spectrum product listings describe Bluetooth app tracking and 6-minute sessions.
- GitHub references to the observed proprietary UART-style service UUID exist in generic BLE contexts, but not Capillus-specific code:
  - `49535343-fe7d-4ae5-8fa9-9fafd205e455`
  - `49535343-1e4d-4bd9-ba61-23c647249616`

This project intentionally uses read-only presence/timing inference rather than reverse-engineering private app commands.
