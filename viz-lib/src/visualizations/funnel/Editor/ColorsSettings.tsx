import React from "react";
import { useDebouncedCallback } from "use-debounce";
import { Section, ColorPicker } from "@/components/visualizations/editor";
import { EditorPropTypes } from "@/visualizations/prop-types";
import ColorPalette from "../../ColorPalette";

export default function ColorsSettings({ options, onOptionsChange }: any) {
  const [onOptionsChangeDebounced] = useDebouncedCallback(onOptionsChange, 200);

  return (
    <React.Fragment>
      {/* @ts-expect-error ts-migrate(2745) FIXME: This JSX tag's 'children' prop expects type 'never... Remove this comment to see the full error message */}
      <Section>
        <ColorPicker
          layout="horizontal"
          label="Color"
          interactive
          presetColors={ColorPalette}
          placement="topRight"
          color={options.color}
          triggerProps={{ "data-test": "Funnel.Editor.Colors.color" }}
          onChange={(color: any) => onOptionsChange({ color: color })}
          // @ts-expect-error ts-migrate(2339) FIXME: Property 'Label' does not exist on type '({ classN... Remove this comment to see the full error message
          addonAfter={<ColorPicker.Label color={options.color} presetColors={ColorPalette} />}
        />
      </Section>
    </React.Fragment>
  );
}

ColorsSettings.propTypes = EditorPropTypes;
