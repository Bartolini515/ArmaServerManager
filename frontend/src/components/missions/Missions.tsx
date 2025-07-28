import { Box, Typography } from "@mui/material";
import MyButton from "../../UI/forms/MyButton";
import { useState } from "react";
import CreateMissionModal from "./CreateMissionModal";

export default function Missions() {
	const [open, setOpen] = useState(false);

	const handleClick = () => {
		setOpen(true);
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
				<Typography variant="h5">Panel misji</Typography>
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
					label={"Naciśnij aby wgrać misję"}
					type={"button"}
					variant="outlined"
					color="primary"
					onClick={handleClick}
				/>
			</Box>
			{open && (
				<CreateMissionModal open={open} onClose={() => setOpen(false)} />
			)}
		</Box>
	);
}
