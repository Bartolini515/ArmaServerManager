import { type Theme, useTheme } from "@mui/material/styles";
import Box from "@mui/material/Box";
import OutlinedInput from "@mui/material/OutlinedInput";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import FormControl from "@mui/material/FormControl";
import Select, { type SelectChangeEvent } from "@mui/material/Select";
import Chip from "@mui/material/Chip";

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

function getStyles(name: string, optionName: readonly string[], theme: Theme) {
	return {
		fontWeight: optionName.includes(name)
			? theme.typography.fontWeightMedium
			: theme.typography.fontWeightRegular,
	};
}

interface Props {
	label: string;
	options: string[];
	setSelectedValue: any;
	selectedValue: string[];
}

export default function MultipleSelectChip(props: Props) {
	const theme = useTheme();

	const handleChange = (
		event: SelectChangeEvent<typeof props.selectedValue>
	) => {
		const {
			target: { value },
		} = event;

		props.setSelectedValue(
			typeof value === "string" ? value.split(",") : value
		);
	};

	return (
		<div>
			<FormControl sx={{ m: 1, width: "100%" }}>
				<InputLabel id="multiple-chip-label">{props.label}</InputLabel>
				<Select
					labelId="multiple-chip-label"
					id="multiple-chip"
					multiple
					value={props.selectedValue}
					onChange={handleChange}
					input={
						<OutlinedInput id="select-multiple-chip" label={props.label} />
					}
					renderValue={(selected) => (
						<Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
							{selected.map((value) => (
								<Chip key={value} label={value} />
							))}
						</Box>
					)}
					MenuProps={MenuProps}
				>
					{props.options.map((option) => (
						<MenuItem
							key={option}
							value={option}
							style={getStyles(option, props.selectedValue, theme)}
						>
							{option}
						</MenuItem>
					))}
				</Select>
			</FormControl>
		</div>
	);
}
