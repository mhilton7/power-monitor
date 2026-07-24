import { UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION } from './generated/utilityAccountRateContext'

const buildEnvironment = import.meta.env as unknown as Record<string, unknown>
const buildVersion = buildEnvironment.VITE_BUILD_VERSION
const releaseCommit = buildEnvironment.VITE_RELEASE_COMMIT

export const FRONTEND_BUILD_VERSION =
  typeof buildVersion === 'string' && buildVersion ? buildVersion : '1.0.0-development'
export const FRONTEND_RELEASE_COMMIT =
  typeof releaseCommit === 'string' && releaseCommit ? releaseCommit : 'development'
export const FRONTEND_API_SCHEMA_VERSION = '1.0.0'
export const FRONTEND_BILL_IMPORT_SCHEMA_VERSION =
  UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION
