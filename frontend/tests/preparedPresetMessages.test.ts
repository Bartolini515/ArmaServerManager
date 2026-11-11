import assert from "node:assert/strict";
import { presetAddedAlert } from "../src/components/moderatorPanel/preparedPresetMessages.ts";

assert.equal(presetAddedAlert({}).severity, "success");
assert.equal(presetAddedAlert({ cleanup_pending: true }).severity, "warning");
assert.match(presetAddedAlert({ cleanup_pending: true }).message, /czyszczenie/);
console.log("Prepared preset alert tests passed.");
