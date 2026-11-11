export function presetAddedAlert(data: { cleanup_pending?: boolean }): { message: string; severity: "warning" | "success" } {
	return data.cleanup_pending
		? { message: "Paczka została dodana; czyszczenie plików wymaga uwagi administratora.", severity: "warning" }
		: { message: "Paczka została dodana.", severity: "success" };
}
