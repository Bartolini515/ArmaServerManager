import Button from "@mui/material/Button";

interface Props {
	label: string;
	type: "submit" | "button" | "reset";
	variant?: "text" | "outlined" | "contained";
	color?: "primary" | "secondary" | "success" | "error" | "info" | "warning";
	style?: import("@mui/system").SxProps<import("@mui/material").Theme>;
	onClick?: () => void;
}

export default function MyButton(props: Props) {
	return (
		<Button
			type={props.type}
			variant={props.variant || "contained"}
			color={props.color || "primary"}
			className={"myButton"}
			onClick={props.onClick}
			sx={props.style}
		>
			{props.label}
		</Button>
	);
}
