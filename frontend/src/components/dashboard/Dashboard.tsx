import Box from "@mui/material/Box";
import SystemResourcesMonitor from "./SystemResourcesMonitor";

export default function Dashboard() {
	return (
		<Box
			sx={{
				flexGrow: { sm: 1, xs: "none" },
				padding: 2,
				alignContent: "center",
			}}
		>
			<Box>
				<SystemResourcesMonitor />
			</Box>
		</Box>
	);
}
