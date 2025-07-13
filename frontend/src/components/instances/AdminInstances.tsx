import InstanceCard from "./InstanceCard";
import { Stack } from "@mui/material";

export default function AdminInstances() {
	return (
		<Stack spacing={2} alignItems="center" width="100%">
			<InstanceCard title="Main Instance 1" />
			<InstanceCard title="Train Instance 2" />
			<InstanceCard title="Liberka Instance 3" />
		</Stack>
	);
}
