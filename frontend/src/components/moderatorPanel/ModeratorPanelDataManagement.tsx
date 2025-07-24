import { Box, Divider, Button } from "@mui/material";
import ModeratorPanelDataManagementTable from "./tables/ModeratorPanelDataManagementTable";
import SingleSelectAutoWidth from "../../UI/forms/SingleSelectAutoWidth";
import { useState } from "react";
import CreateForPanelDataModal from "./modals/CreateForPanelDataModal";

export default function ModeratorPanelDataManagement() {
	const [selectedOption, setSelectedOption] =
		useState<keyof typeof optionsMap>("user");
	const [open, setOpen] = useState(false);
	const [refresh, setRefresh] = useState(false);

	const optionsMap = {
		user: {
			name: "user",
			label: "Użytkownicy",
			labelSingle: "Użytkownika",
			headers: ["ID", "Nazwa użytkownika", "Ostatnie logowanie"],
			buttonAdd: "Dodaj użytkownika",
			forms: {
				first_field: {
					title: "Nazwa użytkownika",
					label: "Nazwa użytkownika",
					name: "username",
				},
			},
			payload: (data: any) => ({
				username: data.username,
				password: data.password,
			}),
		},
	};

	const handleClick = () => {
		setOpen(true);
	};

	return (
		<Box
			sx={{
				boxShadow: 3,
				padding: "20px",
			}}
		>
			<Box sx={{ width: "30%" }}>
				<SingleSelectAutoWidth
					label="Tabela"
					options={Object.keys(optionsMap).map((key, index) => ({
						id: index,
						option: key,
						label: optionsMap[key as keyof typeof optionsMap].label,
					}))}
					setSelectedOption={setSelectedOption}
					selectedOption={selectedOption}
				/>
			</Box>

			<ModeratorPanelDataManagementTable
				option={optionsMap[selectedOption]}
				refresh={refresh}
				setRefresh={setRefresh}
			/>
			<Divider sx={{ marginBottom: "20px" }} />
			<Button variant="contained" color="primary" onClick={handleClick}>
				{optionsMap[selectedOption].buttonAdd}
			</Button>
			{open && (
				<CreateForPanelDataModal
					option={optionsMap[selectedOption]}
					open={open}
					setOpen={setOpen}
					setRefresh={setRefresh}
				/>
			)}
		</Box>
	);
}
