import "../../App.css";
import TextField from "@mui/material/TextField";
import { Controller } from "react-hook-form";

interface Props {
	label: string;
	name: string;
	control: any;
	variant?: "standard" | "filled" | "outlined";
	style?: import("@mui/system").SxProps<import("@mui/material").Theme>;
	multiline?: boolean;
	maxRows?: number;
	helperText?: string;
}

export default function MyTextField(props: Props) {
	return (
		<Controller
			name={props.name}
			control={props.control}
			render={({ field: { onChange, value }, fieldState: { error } }) => (
				<TextField
					id="outlined-basic"
					onChange={onChange}
					value={value || ""}
					label={props.label}
					variant={props.variant || "outlined"}
					sx={props.style || { width: "100%" }}
					multiline={props.multiline || false}
					maxRows={props.maxRows || 1}
					className={"myForm"}
					error={!!error}
					helperText={error ? error.message : props?.helperText}
				/>
			)}
		/>
	);
}
