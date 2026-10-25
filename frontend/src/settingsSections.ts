/* The sections of Settings — listed under Settings in the sidebar while it is open, one shown at a time
 * (design/settings-layout.html). */
export const SETTINGS_SECTIONS = [
  { id: 'general', title: 'General' },
  { id: 'novelai', title: 'NovelAI' },
  { id: 'vaults', title: 'Vaults' },
  { id: 'agents', title: 'Agents' },
  { id: 'activity', title: 'Agent activity' },
] as const
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]['id']
