import net from 'node:net';
import { spawn } from 'node:child_process';

function isPortOpen(host, port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    socket.setTimeout(500);
    socket.once('connect', () => {
      socket.destroy();
      resolve(true);
    });
    socket.once('timeout', () => {
      socket.destroy();
      resolve(false);
    });
    socket.once('error', () => resolve(false));
  });
}

if (await isPortOpen('127.0.0.1', 5173)) {
  console.log('Reusing existing Vite dev server on http://127.0.0.1:5173');
  process.exit(0);
}

const child = spawn('npm', ['run', 'dev'], {
  stdio: 'inherit',
  shell: false,
});

child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code ?? 0);
});
