import Box from "@mui/material/Box";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import FormControl from "@mui/material/FormControl";
import Select, { type SelectChangeEvent } from "@mui/material/Select";

interface Props {
	options: { id: number; option: string }[];
	selectedOption: any;
	setSelectedOption: (value: any) => void;
	label: string;
}

export default function BasicSelect(props: Props) {
	const handleChange = (event: SelectChangeEvent) => {
		props.setSelectedOption(event.target.value as string);
	};

	return (
		<Box sx={{ minWidth: 120, margin: "8px" }}>
			<FormControl fullWidth>
				<InputLabel id="simple-select-label">{props.label}</InputLabel>
				<Select
					labelId="simple-select-label"
					id="simple-select"
					value={props.selectedOption}
					label={props.label}
					onChange={handleChange}
					className={"myForm"}
				>
					{props.options.map((option) => (
						<MenuItem key={option.id} value={option.id}>
							{option.option}
						</MenuItem>
					))}
				</Select>
			</FormControl>
		</Box>
	);
}
