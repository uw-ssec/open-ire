# SharePoint Integration

Open IRE uses the Microsoft Graph API to upload article files and database
snapshots to a SharePoint document library. Authentication uses an Azure AD app
registration with client credentials, so no interactive user sign-in is
required.

> [!NOTE]
>
> The SharePoint pipeline is environment-aware: disabled in development by
> default and active in production.

## Managing the Integration

### Azure App Registration

The integration uses an
[app registration](https://learn.microsoft.com/en-us/security/zero-trust/develop/app-registration)
Microsoft Entra ID (formerly Azure AD). This provides the application with an
identity that can call the Graph API on behalf of the organization, without
requiring a user account.

Management links:

- [App overview](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Overview/appId/1c8e7535-f420-440d-be38-881eff0193a6/isMSAApp~/false)
- [Owners](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Owners/appId/1c8e7535-f420-440d-be38-881eff0193a6/isMSAApp~/false)
- [Client secrets](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Credentials/appId/1c8e7535-f420-440d-be38-881eff0193a6/isMSAApp~/false)

### Credentials

The integration requires four environment variables (see `.env.example`):

| Variable                   | Azure portal equivalent |
| -------------------------- | ----------------------- |
| `SHAREPOINT_CLIENT_ID`     | Application (client) ID |
| `SHAREPOINT_TENANT_ID`     | Directory (tenant) ID   |
| `SHAREPOINT_SITE_ID`       | SharePoint site ID      |
| `SHAREPOINT_CLIENT_SECRET` | Client secret value     |

> [!WARNING]
>
> Client secrets have an expiration date. The current secret expires on
> **2028-02-18**. When a secret expires, the pipeline will fail to authenticate.
> Rotate secrets through the
> [Credentials](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Credentials/appId/1c8e7535-f420-440d-be38-881eff0193a6/isMSAApp~/false)
> page before they expire.

### SharePoint Drive

Files are uploaded to the
[Shared Documents](https://uwnetid.sharepoint.com/sites/uw-scholarship-archiving/Shared%20Documents/Forms/AllItems.aspx)
library, organized under a base path controlled by the `SHAREPOINT_BASE_PATH`
setting (defaults to `open_ire_dev` in development and `open_ire` in
production).

## Requesting a New Integration

Steps to set up a new SharePoint integration for a different site or drive. For
more details on this process, see the
[UW-IT article](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034051).

### 1. Register an app in Microsoft Entra ID

- Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com)
- Navigate to App registrations > New registration
- Set a descriptive name and select "Accounts in this organizational directory
  only" (single-tenant)
- Record the Application (client) ID and Directory (tenant) ID from the overview
  page

Reference:
[Register an app](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)

### 2. Configure API permissions

- In the app registration, go to API permissions > Add a permission
- Select Microsoft Graph > Application permissions (not Delegated)
- Add the `Sites.Selected` permission
- An admin must grant consent for the tenant

> [!IMPORTANT]
>
> `Sites.Selected` grants access only to specific SharePoint sites that are
> explicitly authorized, rather than all sites in the tenant. This is the
> least-privilege approach recommended by Microsoft.

### 3. Request UW-IT approval

UW-IT must authorize the app's access to the target SharePoint site. Submit a
request through the
[UW-IT form](https://uw.service-now.com/it?id=sc_cat_item&sys_id=3def24da6ff3660054aacd16ad3ee4e5).
Include the Application (client) ID and the SharePoint site URL in the request.

### 4. Create a client secret

- In the app registration, go to Certificates & secrets > New client secret
- Set a description and expiration (maximum 24 months)
- Copy the secret value to the `.env` file

Reference:
[Generate a client secret](https://learn.microsoft.com/en-us/azure/industry/training-services/microsoft-community-training/public-preview-version/frequently-asked-questions/generate-new-clientsecret-link-to-key-vault)

### 5. Configure environment variables

Set the environment variables described in the [Credentials](#credentials)
section above, plus `SHAREPOINT_BASE_PATH` for the desired upload directory.
