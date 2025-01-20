import createTabbedEditor from "@/components/visualizations/editor/createTabbedEditor";

import GeneralSettings from "./GeneralSettings";
import AppearanceSettings from "./AppearanceSettings";
import ColorsSettings from "./ColorsSettings";

export default createTabbedEditor([
  { key: "General", title: "General", component: GeneralSettings },
  { key: "Appearance", title: "Appearance", component: AppearanceSettings },
  { key: "Colors", title: "Colors", component: ColorsSettings },
]);
