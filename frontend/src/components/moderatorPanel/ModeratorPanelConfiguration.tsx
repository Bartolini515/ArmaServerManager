import { Box, Typography } from "@mui/material";
import MyButton from "../../UI/forms/MyButton";
import { useState } from "react";
import ConfigModeratorPanelModal from "./modals/ConfigModeratorPanelModal";

export default function ModeratorPanelConfiguration() {
	const [option, setOption] = useState<keyof typeof optionsMap>("schedule");
	const [open, setOpen] = useState(false);

	const optionsMap = {
		schedule: {
			name: "schedule",
			axiosUrl: "schedule/create/",
			labelModal: "Stwórz plan lekcji",
			buttonSend: "Stwórz plan lekcji",
			forms: {
				first_field: {
					title: "URL",
					label: "Wprowadź URL",
					name: "url",
					helperText:
						"Podaj URL do planu lekcji, np. 'https://plan.zset.leszno.pl/'",
				},
			},
			payload: (data: any) => ({
				url: data.url,
			}),
		},
	};

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
					label={"Stwórz plan lekcji"}
					type={"button"}
					variant="outlined"
					color="primary"
					onClick={() => handleClick("schedule")}
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
