// `npm run tauri:build:release` builds the signed updater artifacts, which `tauri build` can only
// sign with the updater private key. Fail fast with a clear message instead of after the compile.
if (!process.env.TAURI_SIGNING_PRIVATE_KEY && !process.env.TAURI_SIGNING_PRIVATE_KEY_PATH) {
  console.error(
    'tauri:build:release signs the updater artifacts and needs the updater private key.\n' +
      'Set TAURI_SIGNING_PRIVATE_KEY (the key content) or TAURI_SIGNING_PRIVATE_KEY_PATH, and\n' +
      'TAURI_SIGNING_PRIVATE_KEY_PASSWORD if the key has a password.\n' +
      'For an unsigned local build use `npm run tauri:build`.',
  );
  process.exit(1);
}
