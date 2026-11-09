import { Stack, Typography } from "@mui/material";
import InstanceCard from "./InstanceCard";
import type { ReconciliationBlocks } from "./taskState";
import type {
	Instance,
	InstanceAction,
	InstanceTaskSet,
	TaskLedger,
} from "./types";

interface Props {
	instances: Instance[];
	tasks: TaskLedger;
	pendingRequests: ReadonlySet<number>;
	reconciliationBlocks: ReconciliationBlocks;
	showOwner?: boolean;
	emptyMessage: string;
	handleClick: (type: InstanceAction, id: number) => void;
}

export default function UserInstances(props: Props) {
	return (
		<Stack spacing={2} alignItems="center" width="100%">
			{props.instances.length > 0 ? (
				props.instances.map((instance) => (
					<InstanceCard
						key={instance.id}
						instance={instance}
						activeTasks={(props.tasks[instance.id] ?? {}) as InstanceTaskSet}
						requestPending={props.pendingRequests.has(instance.id)}
						reconciling={props.reconciliationBlocks.has(instance.id)}
						showOwner={props.showOwner}
						handleClick={props.handleClick}
					/>
				))
			) : (
				<Typography variant="body1" color="textSecondary" fontSize={20}>
					{props.emptyMessage}
				</Typography>
			)}
		</Stack>
	);
}
