import { useEffect, useState } from "react";
import { FormControl, InputLabel, MenuItem, Select, FormHelperText } from "@mui/material";
import AxiosInstance from "../../AxiosInstance";

export type PreparedPreset = { id: number; name: string; preset_file: string };

export default function PreparedPresetSelect({ value, onChange }: { value: number | ""; onChange: (value: number | "") => void }) {
	const [presets, setPresets] = useState<PreparedPreset[]>([]);
	const [error, setError] = useState(false);
	useEffect(() => {
		const controller = new AbortController();
		AxiosInstance.get<PreparedPreset[]>("prepared-presets/", { signal: controller.signal })
			.then(response => setPresets(response.data))
			.catch(() => { if (!controller.signal.aborted) setError(true); });
		return () => controller.abort();
	}, []);
	return <FormControl fullWidth margin="normal" error={error}>
		<InputLabel id="preset-source-label">Źródło presetu</InputLabel>
		<Select labelId="preset-source-label" label="Źródło presetu" value={value} onChange={event => onChange(event.target.value === "" ? "" : Number(event.target.value))}>
			<MenuItem value="">Własny plik</MenuItem>
			{presets.map(preset => <MenuItem key={preset.id} value={preset.id}>{preset.name}</MenuItem>)}
		</Select>
		{error && <FormHelperText>Nie udało się pobrać przygotowanych paczek.</FormHelperText>}
	</FormControl>;
}
