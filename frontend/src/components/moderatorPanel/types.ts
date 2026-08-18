export interface UserModeratorFormData {
	username?: string;
	password?: string;
}

export interface ConfigModeratorFormData {
	steamcmd?: string;
	arma3?: string;
	mods_directory?: string;
	logs_directory?: string;
	download_directory?: string;
	username?: string;
	password?: string;
	shared_secret?: string;
}

export interface ModeratorFormOption {
	first_field: {
		title: string;
		label: string;
		name: string;
		helperText?: string;
	};
}

export interface UserModeratorOption {
	name: string;
	label: string;
	labelSingle: string;
	headers: string[];
	buttonAdd: string;
	forms: ModeratorFormOption;
	payload: (data: UserModeratorFormData) => Record<string, unknown>;
}

export interface ConfigModeratorOption {
	name: string;
	axiosUrl: string;
	labelModal: string;
	buttonSend: string;
	forms: {
		first_field: Omit<ModeratorFormOption["first_field"], "title"> & {
			title: string | null;
		};
	};
	payload: (data: ConfigModeratorFormData) => Record<string, unknown>;
}
