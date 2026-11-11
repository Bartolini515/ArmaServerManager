import assert from "node:assert/strict";
import { limitLogLines, shouldFollowLog } from "../src/components/instances/modals/logBuffer.ts";

const lines = Array.from({ length: 101 }, (_, index) => `line ${index}`);
assert.equal(limitLogLines(lines.join("\n"), 100), lines.slice(1).join("\n"));
assert.equal(limitLogLines(lines.join("\n") + "\n", 100), lines.slice(1).join("\n") + "\n");
assert.equal(limitLogLines("one\ntwo", 100), "one\ntwo");
assert.equal(shouldFollowLog({ scrollHeight: 1000, scrollTop: 200, clientHeight: 300 }), false);
assert.equal(shouldFollowLog({ scrollHeight: 1000, scrollTop: 700, clientHeight: 300 }), true);
console.log("Log buffer regression tests passed.");
