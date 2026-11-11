import { Box, Divider, Button } from "@mui/material";
import ModeratorPanelDataManagementTable from "./tables/ModeratorPanelDataManagementTable";
import SingleSelectAutoWidth from "../../UI/forms/SingleSelectAutoWidth";
import { useState } from "react";
import CreateForPanelDataModal from "./modals/CreateForPanelDataModal";
import type { UserModeratorOption } from "./types";
import MissionManagement from "./MissionManagement";

const optionsMap: Record<"user", UserModeratorOption> = {
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
		payload: (data) => ({
			username: data.username,
			password: data.password,
		}),
	},
};

export default function ModeratorPanelDataManagement() {
	const [selectedOption, setSelectedOption] =
		useState<"user" | "mission">("user");
	const [open, setOpen] = useState(false);
	const [refresh, setRefresh] = useState(false);
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
					options={[{ id: 0, option: "user", label: "Użytkownicy" }, { id: 1, option: "mission", label: "Misje" }]}
					setSelectedOption={(value) =>
						setSelectedOption(value as "user" | "mission")
					}
					selectedOption={selectedOption}
				/>
			</Box>

			{selectedOption === "mission" ? <MissionManagement /> : <><ModeratorPanelDataManagementTable
				option={optionsMap.user}
				refresh={refresh}
				setRefresh={setRefresh}
			/>
			<Divider sx={{ marginBottom: "20px" }} />
			<Button variant="contained" color="primary" onClick={handleClick}>
				{optionsMap.user.buttonAdd}
			</Button>
			{open && (
				<CreateForPanelDataModal
					option={optionsMap.user}
					open={open}
					setOpen={setOpen}
					setRefresh={setRefresh}
				/>
			)}</>}
		</Box>
	);
}
