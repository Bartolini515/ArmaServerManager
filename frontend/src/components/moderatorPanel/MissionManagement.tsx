import { useCallback, useEffect, useState } from "react";
import { Box, Button, List, ListItem, ListItemText, Typography } from "@mui/material";
import AxiosInstance from "../AxiosInstance";
import AlertDialog from "../../UI/dialogs/AlertDialog";
import { useAlert } from "../../contexts/AlertContext";
import { getApiErrorMessage } from "../../util/api-error";

type Mission = { id: number; mission_file: string };

export default function MissionManagement() {
	const [missions, setMissions] = useState<Mission[]>([]);
	const [selected, setSelected] = useState<Mission | null>(null);
	const [pending, setPending] = useState(false);
	const { setAlert } = useAlert();
	const load = useCallback(async () => {
		try {
			const response = await AxiosInstance.get<Mission[]>("missions/");
			setMissions(response.data);
		} catch (error: unknown) {
			setAlert(getApiErrorMessage(error, "Nie udało się pobrać misji."), "error");
		}
	}, [setAlert]);
	useEffect(() => { void load(); }, [load]);
	const remove = async () => {
		if (!selected || pending) return;
		setPending(true);
		try {
			const response = await AxiosInstance.delete(`missions/${selected.id}/`);
			setAlert(response.data?.message ?? "Misja została usunięta.", "success");
			setSelected(null);
			await load();
		} catch (error: unknown) {
			setAlert(getApiErrorMessage(error, "Nie udało się usunąć misji."), "error");
		} finally { setPending(false); }
	};
	return <Box>
		<Typography>Misję można usunąć tylko wtedy, gdy żadna instancja nie działa.</Typography>
		<List>{missions.map(mission => <ListItem key={mission.id} secondaryAction={<Button color="error" onClick={() => setSelected(mission)}>Usuń</Button>}>
			<ListItemText primary={mission.mission_file.split("/").pop()} />
		</ListItem>)}</List>
		{selected && <AlertDialog open onClose={() => setSelected(null)} label="Usuń misję" content={`Czy na pewno usunąć ${selected.mission_file.split("/").pop()}?`} onCloseOption2={() => { void remove(); }} />}
	</Box>;
}
