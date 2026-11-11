import { useCallback, useEffect, useState } from "react";
import { Box, Button, List, ListItem, ListItemText, TextField, Typography } from "@mui/material";
import AxiosInstance from "../AxiosInstance";
import AlertDialog from "../../UI/dialogs/AlertDialog";
import { useAlert } from "../../contexts/AlertContext";
import { getApiErrorData, getApiErrorMessage, getFieldErrorMessage } from "../../util/api-error";
import type { PreparedPreset } from "../instances/modals/PreparedPresetSelect";

export default function PreparedPresetManagement() {
	const [presets, setPresets] = useState<PreparedPreset[]>([]);
	const [selected, setSelected] = useState<PreparedPreset | null>(null);
	const [name, setName] = useState("");
	const [file, setFile] = useState<File | null>(null);
	const [busy, setBusy] = useState(false);
	const [fileKey, setFileKey] = useState(0);
	const { setAlert } = useAlert();
	const load = useCallback(async () => {
		try { setPresets((await AxiosInstance.get<PreparedPreset[]>("prepared-presets/")).data); }
		catch (error: unknown) { setAlert(getApiErrorMessage(error, "Nie udało się pobrać paczek."), "error"); }
	}, [setAlert]);
	useEffect(() => { void load(); }, [load]);
	const add = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!file || busy) return;
		const payload = new FormData();
		payload.append("name", name);
		payload.append("preset_file", file);
		setBusy(true);
		try {
			await AxiosInstance.post("prepared-presets/", payload, { headers: { "Content-Type": "multipart/form-data" } });
			setAlert("Paczka została dodana.", "success");
			setName(""); setFile(null); setFileKey(value => value + 1);
			await load();
		} catch (error: unknown) {
			const errors = getApiErrorData(error);
			setAlert((errors && (getFieldErrorMessage(errors, "name") || getFieldErrorMessage(errors, "preset_file"))) || getApiErrorMessage(error, "Nie udało się dodać paczki."), "error");
		} finally { setBusy(false); }
	};
	const remove = async () => {
		if (!selected || busy) return;
		setBusy(true);
		try {
			const response = await AxiosInstance.delete(`prepared-presets/${selected.id}/`);
			setAlert(response.data?.message ?? "Paczka została usunięta.", "success");
			setSelected(null); await load();
		} catch (error: unknown) { setAlert(getApiErrorMessage(error, "Nie udało się usunąć paczki."), "error"); }
		finally { setBusy(false); }
	};
	return <Box>
		<Typography variant="h6">Przygotowane paczki</Typography>
		<List>{presets.map(preset => <ListItem key={preset.id} secondaryAction={<Button color="error" onClick={() => setSelected(preset)}>Usuń</Button>}><ListItemText primary={preset.name} secondary={preset.preset_file.split("/").pop()} /></ListItem>)}</List>
		<Box component="form" onSubmit={add} sx={{ display: "flex", gap: 2, flexWrap: "wrap", alignItems: "center" }}>
			<TextField label="Nazwa paczki" required value={name} onChange={event => setName(event.target.value)} inputProps={{ maxLength: 255 }} />
			<input key={fileKey} aria-label="Plik presetu" type="file" required onChange={event => setFile(event.target.files?.[0] ?? null)} />
			<Button type="submit" disabled={busy} variant="contained">Dodaj paczkę</Button>
		</Box>
		{selected && <AlertDialog open onClose={() => setSelected(null)} label="Usuń paczkę" content={`Czy na pewno usunąć paczkę ${selected.name}?`} onCloseOption2={() => { void remove(); }} />}
	</Box>;
}
