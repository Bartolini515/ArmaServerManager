import "../../App.css";
import { Controller } from "react-hook-form";
import { useDropzone } from "react-dropzone";
import { type FC, type ChangeEventHandler, useMemo, useState } from "react";

interface Props {
	label: string;
	name: string;
	control: any;
	multiple?: boolean;
	maxFiles?: number;
	accept?: string | string[];
	style?: import("@mui/system").SxProps<import("@mui/material").Theme>;
	onSubmit?: (files: File[]) => void;
	rest?: any;
}

export default function MyDropzone(props: Props) {
	return (
		<Controller
			name={props.name}
			control={props.control}
			render={({ field: { onChange } }) => (
				<Dropzone
					multiple={props.multiple}
					maxFiles={props.multiple}
					onChange={(e) =>
						onChange(
							props.multiple ? e.target.files : e.target.files?.[0] ?? null
						)
					}
					onSubmit={props.onSubmit}
					accept={props.accept}
					style={props.style}
					{...props.rest}
				/>
			)}
		/>
	);
}

const baseStyle = {
	flex: 1,
	display: "flex",
	flexDirection: "column",
	alignItems: "center",
	padding: "20px",
	borderWidth: 2,
	borderRadius: 2,
	borderColor: "#eeeeee",
	borderStyle: "dashed",
	backgroundColor: "#fafafa",
	color: "#bdbdbd",
	outline: "none",
	transition: "border .24s ease-in-out",
};

const focusedStyle = {
	borderColor: "#2196f3",
};

const acceptStyle = {
	borderColor: "#00e676",
};

const rejectStyle = {
	borderColor: "#ff1744",
};

const Dropzone: FC<{
	multiple?: boolean;
	maxFiles?: number;
	onChange?: ChangeEventHandler<HTMLInputElement>;
	style?: React.CSSProperties;
	onSubmit?: (files: File[]) => void;
	accept?: string | string[];
}> = ({
	multiple,
	maxFiles,
	onChange,
	style: customStyle,
	onSubmit,
	accept,
	...rest
}) => {
	const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);

	const { getRootProps, getInputProps, isFocused, isDragAccept, isDragReject } =
		useDropzone({
			multiple,
			maxFiles,
			accept: Array.isArray(accept)
				? Object.fromEntries(accept.map((type) => [type, []]))
				: accept
				? { [accept]: [] }
				: undefined,
			onDropAccepted: (acceptedFiles) => {
				setUploadedFiles((prev) => [...prev, ...acceptedFiles]);
				if (onSubmit) {
					onSubmit(acceptedFiles);
				}
			},
			...rest,
		});

	const style = useMemo(
		() => ({
			...baseStyle,
			...customStyle,
			...(isFocused ? focusedStyle : {}),
			...(isDragAccept ? acceptStyle : {}),
			...(isDragReject ? rejectStyle : {}),
		}),
		[isFocused, isDragAccept, isDragReject]
	);

	return (
		<div className="container">
			<div {...getRootProps({ style: style as React.CSSProperties })}>
				<input {...getInputProps({ onChange })} />
				<p>
					{multiple
						? "Wybierz pliki lub przeciągnij je tutaj"
						: "Wybierz plik lub przeciągnij go tutaj"}
				</p>
			</div>
			<div>
				{uploadedFiles.length > 0 && (
					<ul>
						{uploadedFiles.map((file, index) => (
							<li key={index}>{file.name}</li>
						))}
					</ul>
				)}
			</div>
		</div>
	);
};
