import { Box, Typography } from "@mui/material";
import MyButton from "../../UI/forms/MyButton";
import { useState } from "react";
import ConfigModeratorPanelModal from "./modals/ConfigModeratorPanelModal";
import { buildConfigPayload } from "./configPayload";
import type { ConfigModeratorOption } from "./types";

const optionsMap: Record<"config", ConfigModeratorOption> = {
	config: {
		name: "config",
		axiosUrl: "config/update/",
		labelModal: "Ustaw konfigurację",
		buttonSend: "Ustaw konfigurację",
		forms: {
			first_field: {
				title: null,
				label: "Ścieżka do folderu steamcmd",
				name: "steamcmd",
				helperText:
					"Podaj ścieżkę do folderu steamcmd, np. '/path/to/steamcmd'",
			},
		},
		payload: buildConfigPayload,
	},
};

export default function ModeratorPanelConfiguration() {
	const [option, setOption] = useState<keyof typeof optionsMap>("config");
	const [open, setOpen] = useState(false);
	const handleClick = (option: keyof typeof optionsMap) => {
		setOpen(true);
		setOption(option);
	};

	return (
		<Box
			sx={{
				boxShadow: 3,
				padding: "20px",
				display: "flex",
				flexDirection: "column",
			}}
		>
			<Box
				sx={{
					display: "flex",
					alignItems: "center",
					justifyContent: "center",
					marginBottom: "20px",
				}}
			>
				{" "}
				<Typography variant="h5">Panel konfiguracyjny</Typography>
			</Box>
			<Box
				sx={{
					display: "flex",
					justifyContent: "space-evenly",
					flexDirection: { xs: "column", sm: "row" },
					flexWrap: "wrap",
					gap: "20px",
					marginBottom: "20px",
					"& > *": {
						sm: {
							flex: "1 1 calc(50% - 20px)",
							maxWidth: "calc(50% - 20px)",
						},
					},
				}}
			>
				<MyButton
					label={"Ustaw config"}
					type={"button"}
					variant="outlined"
					color="primary"
					onClick={() => handleClick("config")}
				/>
			</Box>
			{open && (
				<ConfigModeratorPanelModal
					option={optionsMap[option]}
					open={open}
					setOpen={setOpen}
				/>
			)}
		</Box>
	);
}
