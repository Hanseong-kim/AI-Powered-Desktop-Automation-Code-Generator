import { execSync } from 'child_process';

describe('MinimalTest', () => {
    it('should run basic test', async () => {
        console.log('[minimal] TEST RUNNING — browser:', typeof browser);
        expect(1).toBe(1);
    });
});
