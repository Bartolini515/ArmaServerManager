import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import FormControl from "@mui/material/FormControl";
import Select, { type SelectChangeEvent } from "@mui/material/Select";
import ListItemText from "@mui/material/ListItemText";

interface Props {
	options: { id: number; option: string; label?: string }[];
	selectedOption: any;
	setSelectedOption: (value: any) => void;
	label: string;
}

export default function SingleSelectAutoWidth(props: Props) {
	const handleChange = (event: SelectChangeEvent) => {
		props.setSelectedOption(event.target.value);
	};

	return (
		<div>
			<FormControl sx={{ m: 1, minWidth: 80 }}>
				<InputLabel id="simple-select-autowidth-label">
					{props.label}
				</InputLabel>
				<Select
					labelId="simple-select-autowidth-label"
					id="simple-select-autowidth"
					value={props.selectedOption}
					onChange={handleChange}
					autoWidth
					label={props.label}
				>
					{props.options.map((option) => (
						<MenuItem key={option.id} value={option.option}>
							<ListItemText primary={option.label || option.option} />
						</MenuItem>
					))}
				</Select>
			</FormControl>
		</div>
	);
}
