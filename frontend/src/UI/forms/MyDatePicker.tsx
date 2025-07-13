import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import "../../App.css";
import { Controller } from "react-hook-form";
import { FormControl, FormHelperText } from "@mui/material";

interface Props {
	label: string;
	name: string;
	helperText?: string;
	disablePast?: boolean;
	maxDate?: Date | null | undefined;
	defaultValue?: Date | null | undefined;
	control: any;
	views?: ["year", "month", "day"];
}

export default function MyDatePicker(props: Props) {
	return (
		<Controller
			name={props.name}
			control={props.control}
			render={({ field: { onChange, value }, fieldState: { error } }) => (
				<FormControl>
					<DatePicker
						name={props.name}
						label={props.label}
						disablePast={props.disablePast}
						maxDate={props.maxDate}
						defaultValue={props.defaultValue}
						views={props.views}
						slotProps={{
							textField: {
								helperText: props.helperText,
							},
						}}
						className={"myForm"}
						onChange={onChange}
						value={value || null}
					/>

					{error && (
						<FormHelperText sx={{ color: "red" }}>
							{error.message}
						</FormHelperText>
					)}
				</FormControl>
			)}
		/>
	);
}
