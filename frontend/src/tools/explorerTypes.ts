export interface ExplorerObject {
  id: string
  type: string
  typeName: string
  name: string
  properties: Record<string, unknown>
  propertyLabels: Record<string, string>
  source?: string
}
export interface ExplorerLink { id: string; type: string; name: string; source: string; target: string }
export interface ExplorerObservation { objectId: string; property: string; value: number; timestamp: string; quality: string; unit: string; source: string }
export interface ExplorerSnapshot { objects: ExplorerObject[]; links: ExplorerLink[]; observations: ExplorerObservation[]; warnings: string[]; errors: string[]; demo: boolean; project: string }
