import * as apiError from "./api-error.ts";

const classify = (apiError as Record<string, unknown>).getCreateInstanceErrors;
if (typeof classify !== "function") {
	throw new Error("Create-instance errors must distinguish form fields from general errors");
}

const result = classify({ name: ["Invalid name"], message: "Files require review" });
if (result.fields.name !== "Invalid name" || result.message !== "Files require review") {
	throw new Error("Both the field error and the general error must be shown");
}

const unknown = classify({ non_field_errors: ["Cannot create instance"] });
if (unknown.message !== "Cannot create instance") {
	throw new Error("Non-field errors must be shown to the user");
}
