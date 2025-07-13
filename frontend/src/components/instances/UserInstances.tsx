import InstanceCard from "./InstanceCard";
import { Stack } from "@mui/material";

export default function UserInstances() {
	return (
		<Stack spacing={2} alignItems="center" width="100%">
			<InstanceCard title="User Instance 1" />
			<InstanceCard title="User Instance 2" />
			<InstanceCard title="User Instance 3" />
		</Stack>
	);
}
