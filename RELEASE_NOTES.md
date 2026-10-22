# New

- A full window with Status, Settings, Help and About tabs, in light or dark.
- Three-step setup on first launch: game settings, your two keys, your resolution.
- Flash Heal: casts when Ana's grenade wears off your ally, or when their health
  drops (Emergency for a sharp fall, Normal for a set health line). Off by default.
- Smooth healing: stays on damage boost through small hits. Off by default.
- Strict focus: a click on another window or monitor never starts Mercy AI.
- Text version in a console: `"Mercy AI.exe" -cli`.
- Diagnostics: `-calibrate`, `-monitors`, `-benchmark`, `-dump-region`. `-help` lists them all.
- A Reset button beside any changed number or key.

# Improvements

- Pause while Overwatch is unfocused is now on by default and out of beta.
- Ana's grenade detection is more reliable and can be turned off.
- The pause key works while other keys are held.
- Your screen resolution is detected and offered during setup.
- Settings are checked before they are saved, with a plain message when
  something is wrong.
- Only one copy of Mercy AI can run at a time.
- One file of about 23 MB, down from about 75 MB.

# Fixes

- Held keys are always released when Mercy AI pauses, stops or closes.
- A damaged `config.xml` opens setup instead of stopping the program.
- The resolution can no longer be set to an unsupported value.

# Upgrading from 1.4.0

- Your `config.xml` and keys carry over. Check them once under Settings.
- Answer Yes to the administrator prompt, or keys will not reach the game.
