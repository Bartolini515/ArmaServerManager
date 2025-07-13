import Box from "@mui/material/Box";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import FormControl from "@mui/material/FormControl";
import Select, { type SelectChangeEvent } from "@mui/material/Select";
import { Controller } from "react-hook-form";
import { FormHelperText } from "@mui/material";

interface Props {
	label: string;
	name: string;
	options: { id: number; option: string; label?: JSX.Element }[];
	control: any;
	selectedOption: any;
	setSelectedOption: (value: any) => void;
	style?: import("@mui/system").SxProps<import("@mui/material").Theme>;
	disabled?: boolean;
}

export default function MySelect(props: Props) {
	const handleChange = (event: SelectChangeEvent) => {
		props.setSelectedOption(event.target.value as string);
	};

	return (
		<Controller
			name={props.name}
			control={props.control}
			render={({ field: { onChange, value }, fieldState: { error } }) => (
				<Box sx={{ minWidth: "20%" }}>
					<FormControl fullWidth>
						<InputLabel id="simple-select-label">{props.label}</InputLabel>
						<Select
							labelId="simple-select-label"
							id="simple-select"
							value={value || props.selectedOption}
							label={props.label}
							onChange={(e) => {
								handleChange(e);
								onChange(e);
							}}
							name={props.name}
							className={"myForm"}
							sx={props.style || { width: "100%" }}
							disabled={props.disabled}
							error={!!error}
						>
							{props.options.map((option) => (
								<MenuItem key={option.id} value={option.id}>
									{option.label || option.option}
								</MenuItem>
							))}
						</Select>
						{error && (
							<FormHelperText sx={{ color: "red" }}>
								{error.message}
							</FormHelperText>
						)}
					</FormControl>
				</Box>
			)}
		/>
	);
}
