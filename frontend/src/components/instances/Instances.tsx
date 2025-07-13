import { Divider, Stack, Typography } from "@mui/material";
import Box from "@mui/material/Box";
import UserInstances from "./UserInstances";
import AdminInstances from "./AdminInstances";

export default function Instances() {
	return (
		<Stack spacing={2} alignItems="center">
			<Box sx={{ width: "100%" }}>
				<Divider sx={{ marginY: 2 }} />
				<Typography align="center" fontWeight={700} fontSize={24}>
					Instancje Użytkownika
				</Typography>
				<Divider sx={{ marginY: 2 }} />
				<UserInstances />
			</Box>
			<Box sx={{ width: "100%" }}>
				<Divider sx={{ marginY: 2 }} />
				<Typography align="center" fontWeight={700} fontSize={24}>
					Instancje Główne
				</Typography>
				<Divider sx={{ marginY: 2 }} />
				<AdminInstances />
			</Box>
		</Stack>
	);
}
