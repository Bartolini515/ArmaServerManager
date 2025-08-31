import { Box, Switch, Typography } from "@mui/material";
import { useCustomTheme, colorMap } from "../../contexts/ThemeContext";

// Derive names and objects directly from the imported colorMap
const themeColorNames = Object.keys(colorMap);
const themeColorObjects = Object.values(colorMap);

export default function Settings() {
	const { mode, primaryColor, toggleMode, setColor } = useCustomTheme();

	return (
		<Box
			sx={{
				boxShadow: 24,
				minWidth: "max-content",
				width: "20%",
				p: 2,
				alignItems: "center",
				flexDirection: "column",
				display: "flex",
				position: "absolute",
				top: "50%",
				left: "50%",
				transform: "translate(-50%, -50%)",
			}}
		>
			<Typography variant="h6" component="h2" sx={{ mb: 2 }}>
				Ustawienia
			</Typography>
			<Box
				sx={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					width: "100%",
				}}
			>
				<Typography>Tryb Ciemny</Typography>
				<Switch onClick={toggleMode} checked={mode === "dark"} />
			</Box>
			<Typography sx={{ width: "100%", mt: 2 }}>Motyw</Typography>
			<Box
				sx={{
					display: "flex",
					flexWrap: "wrap",
					gap: "10px",
					justifyContent: "center",
					mt: 1,
				}}
			>
				{themeColorObjects.map((colorObject, index) => (
					<Box
						key={themeColorNames[index]}
						sx={{
							backgroundColor: (colorObject as { [key: number]: string })[500],
							width: "50px",
							height: "50px",
							borderRadius: "8px",
							cursor: "pointer",
							"&:hover": {
								opacity: 0.8,
							},
							border:
								primaryColor === colorObject
									? "3px solid"
									: "3px solid transparent",
							borderColor:
								primaryColor === colorObject
									? mode === "light"
										? "#000"
										: "#fff"
									: "transparent",
						}}
						onClick={() => setColor(themeColorNames[index])}
					/>
				))}
			</Box>
		</Box>
	);
}
