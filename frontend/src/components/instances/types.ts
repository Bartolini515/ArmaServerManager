export const TASK_KINDS = ["start", "stop", "download"] as const;

export type TaskKind = (typeof TASK_KINDS)[number];
export type InstanceAction = TaskKind | "delete" | "logs" | "preset";
export type LifecycleState =
	| "stopped"
	| "starting"
	| "running"
	| "stopping"
	| "unknown";

export interface InstanceUser {
	id: number;
	username: string;
	last_login: string;
}

export interface TrackedTask {
	taskId: string;
	state: string;
	status: string;
}

export type InstanceTaskBindings = Partial<Record<TaskKind, string>>;
export type InstanceTaskSet = Partial<Record<TaskKind, TrackedTask>>;
export type TaskLedger = Record<number, InstanceTaskSet>;

export interface Instance {
	id: number;
	name: string;
	user: InstanceUser;
	preset: string;
	log_file: string | null;
	start_file_path: string;
	port: string;
	pid: number;
	is_ready: boolean;
	is_running: boolean;
	is_admin_instance: boolean;
	created_at: string;
	lifecycle_state?: LifecycleState;
	shutdown_at?: string | null;
	active_tasks?: InstanceTaskBindings | null;
}

export interface InstanceListResponse {
	user_instances?: Instance[];
	admin_instances?: Instance[];
	other_instances?: Instance[];
}
