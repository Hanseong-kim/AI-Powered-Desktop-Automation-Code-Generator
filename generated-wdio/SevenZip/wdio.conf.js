import { execSync } from 'child_process';

export const config = {
  runner: 'local',
  specs: ['./SevenZipTestById.js', './SevenZipTestByClass.js'],
  exclude: ['./wdio.conf.js'],
  maxInstances: 1,
  capabilities: [{
    platformName: 'Windows',
    'appium:automationName': 'Windows',
    'appium:app': 'C:\\Program Files\\7-Zip\\7zFM.exe',
    'appium:newCommandTimeout': 60000,
    'appium:connectHardwareKeyboard': false,
  }],
  hostname: '127.0.0.1',
  port: 4723,
  path: '/',
  framework: 'jasmine',
  jasmineOpts: { defaultTimeoutInterval: 60000 },
  reporters: ['spec'],
  services: ['appium'],
  appium: { command: 'appium', args: ['--allow-insecure', 'winappdriver'] },
  injectGlobals: true,
  onWorkerStart: function () {
    try {
      execSync('powershell -NoProfile -EncodedCommand dAByAHkAIAB7ACAAUgBlAG0AbwB2AGUALQBJAHQAZQBtAFAAcgBvAHAAZQByAHQAeQAgAC0AUABhAHQAaAAgACcASABLAEMAVQA6AFwAUwBvAGYAdAB3AGEAcgBlAFwANwAtAFoAaQBwAFwARgBNACcAIAAtAE4AYQBtAGUAIABQAGEAbgBlAGwAUABhAHQAaAAwACwAUABhAG4AZQBsAFAAYQB0AGgAMQAgAC0ARQByAHIAbwByAEEAYwB0AGkAbwBuACAAUwB0AG8AcAAgAH0AIABjAGEAdABjAGgAIAB7AH0AOwAgAGUAeABpAHQAIAAwAA==', { stdio: 'pipe', timeout: 10000 });
    } catch (e) {
      console.warn('[onWorkerStart] app-state reset failed (non-fatal):', String(e.message || e).substring(0, 150));
    }
  },
};