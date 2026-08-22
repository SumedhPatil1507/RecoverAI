// Azure App Service deployment via Bicep
// Deploy: az deployment group create --resource-group RecoverAI --template-file azure/webapp.bicep

@description('Location for all resources')
param location string = resourceGroup().location

@description('App Service Plan SKU')
param sku string = 'B1'

@secure()
param razorpayWebhookSecret string

@secure()
param openaiApiKey string = ''

var appServicePlanName = 'recoverai-plan'
var apiAppName         = 'recoverai-api-${uniqueString(resourceGroup().id)}'
var dashboardAppName   = 'recoverai-dash-${uniqueString(resourceGroup().id)}'

// ── App Service Plan ────────────────────────────────────────────────────────
resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: appServicePlanName
  location: location
  sku: {
    name: sku
    tier: 'Basic'
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

// ── FastAPI App ─────────────────────────────────────────────────────────────
resource apiApp 'Microsoft.Web/sites@2023-01-01' = {
  name: apiAppName
  location: location
  properties: {
    serverFarmId: appServicePlan.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'uvicorn recover_ai.main:app --host 0.0.0.0 --port 8000'
      appSettings: [
        { name: 'RAZORPAY_WEBHOOK_SECRET'; value: razorpayWebhookSecret }
        { name: 'OPENAI_API_KEY';          value: openaiApiKey }
        { name: 'DATABASE_PATH';           value: '/home/recover_ai.db' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'; value: 'true' }
      ]
    }
    httpsOnly: true
  }
}

// ── Streamlit Dashboard App ─────────────────────────────────────────────────
resource dashApp 'Microsoft.Web/sites@2023-01-01' = {
  name: dashboardAppName
  location: location
  properties: {
    serverFarmId: appServicePlan.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'streamlit run streamlit_app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true'
      appSettings: [
        { name: 'RAZORPAY_WEBHOOK_SECRET'; value: razorpayWebhookSecret }
        { name: 'OPENAI_API_KEY';          value: openaiApiKey }
        { name: 'DATABASE_PATH';           value: '/home/recover_ai.db' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'; value: 'true' }
      ]
    }
    httpsOnly: true
  }
}

output apiUrl      string = 'https://${apiApp.properties.defaultHostName}'
output dashboardUrl string = 'https://${dashApp.properties.defaultHostName}'
