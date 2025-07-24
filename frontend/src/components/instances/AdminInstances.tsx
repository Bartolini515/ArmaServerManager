import InstanceCard from "./InstanceCard";
import { Stack } from "@mui/material";

interface Instance {
	id: number;
	name: string;
	user: string;
	preset: string;
	created_at: string;
	is_admin_instance: boolean;
	is_ready: boolean;
	is_running: boolean;
	port: number;
}

interface Props {
	adminInstances: Instance[];
}

export default function AdminInstances(props: Props) {
	return (
		<Stack spacing={2} alignItems="center" width="100%">
			{/* {props.adminInstances.map((instance) => (
				<InstanceCard key={instance.id} title={instance.name} status={instance.status} port={instance.port} preset={instance.preset} is_running={instance.is_running} is_ready={instance.is_ready} />
			))} */}
		</Stack>
	);
}
