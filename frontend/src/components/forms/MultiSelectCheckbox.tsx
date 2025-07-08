import OutlinedInput from "@mui/material/OutlinedInput";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import FormControl from "@mui/material/FormControl";
import ListItemText from "@mui/material/ListItemText";
import Select, { type SelectChangeEvent } from "@mui/material/Select";
import Checkbox from "@mui/material/Checkbox";

const ITEM_HEIGHT = 48;
const ITEM_PADDING_TOP = 8;
const MenuProps = {
	PaperProps: {
		style: {
			maxHeight: ITEM_HEIGHT * 4.5 + ITEM_PADDING_TOP,
			width: 250,
		},
	},
};

interface Props {
	label: string;
	options: string[];
	setSelectedValue: any;
	selectedValue: string[];
}

export default function MultiSelectCheckbox(props: Props) {
	const handleChange = (
		event: SelectChangeEvent<typeof props.selectedValue>
	) => {
		const {
			target: { value },
		} = event;

		if (value.includes("all")) {
			props.setSelectedValue(
				props.selectedValue.length === props.options.length ? [] : props.options
			);
		} else {
			props.setSelectedValue(
				typeof value === "string" ? value.split(",") : value
			);
		}
	};

	return (
		<div>
			<FormControl sx={{ m: 1, width: "100%", minWidth: 120 }}>
				<InputLabel id="multiple-checkbox-label">{props.label}</InputLabel>
				<Select
					labelId="multiple-checkbox-label"
					id="multiple-checkbox"
					multiple
					value={props.selectedValue}
					onChange={handleChange}
					input={<OutlinedInput label={props.label} />}
					renderValue={(selected) => selected.join(", ")}
					MenuProps={MenuProps}
				>
					<MenuItem value={"all"}>
						<Checkbox
							checked={props.selectedValue.length === props.options.length}
							indeterminate={
								props.selectedValue.length > 0 &&
								props.selectedValue.length < props.options.length
							}
						/>
						<ListItemText primary={"Zaznacz wszystko"} />
					</MenuItem>

					{props.options.map((option) => (
						<MenuItem key={option} value={option}>
							<Checkbox checked={props.selectedValue.indexOf(option) > -1} />
							<ListItemText primary={option} />
						</MenuItem>
					))}
				</Select>
			</FormControl>
		</div>
	);
}
