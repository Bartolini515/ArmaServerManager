export function limitLogLines(text: string, count: number): string {
	const trailingNewline = text.endsWith("\n");
	const lines = text.split("\n");
	if (trailingNewline) lines.pop();
	const result = lines.slice(-count).join("\n");
	return trailingNewline ? result + "\n" : result;
}

export function shouldFollowLog(element: { scrollHeight: number; scrollTop: number; clientHeight: number }): boolean {
	return element.scrollHeight - element.scrollTop - element.clientHeight < 20;
}
