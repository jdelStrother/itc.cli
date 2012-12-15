# coding=utf-8

import os
import re
import json
import logging
from collections import namedtuple

import requests

from itc.core.inapp import ITCInappPurchase
from itc.parsers.applicationparser import ITCApplicationParser
from itc.util import languages
from itc.util import getElement
from itc.util import EnhancedFile
from itc.conf import *

class ITCApplication(object):
    def __init__(self, name=None, applicationId=None, link=None, dict=None):
        if (dict):
            name = dict['name']
            link = dict['applicationLink']
            applicationId = dict['applicationId']

        self.name = name
        self.applicationLink = link
        self.applicationId = applicationId
        self.versions = {}
        self.inapps = {}

        self._uploadSessionData = {}
        self._images = {}
        self._manageInappsLink = None
        self._manageInappsTree = None
        self._createInappLink = None
        self._inappActionURLs = None
        self._parser = ITCApplicationParser()

        logging.info('Application found: ' + self.__str__())


    def __repr__(self):
        return self.__str__()


    def __str__(self):
        strng = ""
        if self.name != None:
            strng += "\"" + self.name + "\""
        if self.applicationId != None:
            strng += " (" + str(self.applicationId) + ")"

        return strng


    def getAppInfo(self):
        if self.applicationLink == None:
            raise 'Can\'t get application versions'

        tree = self._parser.parseTreeForURL(self.applicationLink)

        # get 'manage in-app purchases' link
        self._manageInappsLink = tree.xpath("//ul[@id='availableButtons']/li/a/span[starts-with(@class, 'in-app')]/../@href")[0]
        logging.debug("Manage In-App purchases link: " + self._manageInappsLink)

        versionsContainer = tree.xpath("//h2[.='Versions']/following-sibling::div")
        if len(versionsContainer) == 0:
            return

        versionDivs = versionsContainer[0].xpath(".//div[@class='version-container']")
        if len(versionDivs) == 0:
            return

        for versionDiv in versionDivs:
            version = {}
            versionString = versionDiv.xpath(".//p/label[.='Version']/../span")[0].text.strip()
            
            version['detailsLink'] = versionDiv.xpath(".//span[.='View Details']/..")[0].attrib['href']
            version['statusString'] = ("".join([str(x) for x in versionDiv.xpath(".//span/img[starts-with(@src, '/itc/images/status-')]/../text()")])).strip()
            version['editable'] = (version['statusString'] != 'Ready for Sale')
            version['versionString'] = versionString

            logging.info("Version found: " + versionString)
            logging.debug(version)

            self.versions[versionString] = version

    def __parseURLSFromScript(self, script):
        matches = re.search('{.*statusURL:\s\'([^\']+)\',\sdeleteURL:\s\'([^\']+)\',\ssortURL:\s\'([^\']+)\'', script) 
        return {'statusURL': matches.group(1)
                , 'deleteURL': matches.group(2)
                , 'sortURL': matches.group(3)}

    def __imagesForDevice(self, device_type):
        if len(self._uploadSessionData) == 0:
            raise 'No session keys found'

        statusURL = self._uploadSessionData[device_type]['statusURL']
        result = None

        if statusURL:
            status = requests.get(ITUNESCONNECT_URL + statusURL
                                  , cookies=cookie_jar)
            statusJSON = json.loads(status.content)
            logging.debug(status.content)
            result = []

            for i in range(0, 5):
                key = 'pictureFile_' + str(i + 1)
                if key in statusJSON:
                    image = {}
                    pictureFile = statusJSON[key]
                    image['url'] = pictureFile['url']
                    image['orientation'] = pictureFile['orientation']
                    image['id'] = pictureFile['pictureId']
                    result.append(image)
                else:
                    break

        return result


    def __uploadScreenshot(self, upload_type, file_path):
        if self._uploadSessionId == None or len(self._uploadSessionData) == 0:
            raise 'Trying to upload screenshot without proper session keys'

        uploadScreenshotAction = self._uploadSessionData[upload_type]['action']
        uploadScreenshotKey = self._uploadSessionData[upload_type]['key']

        if uploadScreenshotAction != None and uploadScreenshotKey != None and os.path.exists(file_path):
            headers = { 'x-uploadKey' : uploadScreenshotKey
                        , 'x-uploadSessionID' : self._uploadSessionId
                        , 'x-original-filename' : os.path.basename(file_path)
                        , 'Content-Type': 'image/png'}
            logging.info('Uploading image ' + file_path)
            r = requests.post(ITUNESCONNECT_URL + uploadScreenshotAction
                                , cookies=cookie_jar
                                , headers=headers
                                , data=EnhancedFile(file_path, 'rb'))

            if r.content == 'success':
                newImages = self.__imagesForDevice(upload_type)
                if len(newImages) > len(self._images[upload_type]):
                    logging.info('Image uploaded')
                else:
                    logging.error('Upload failed: ' + file_path)


    def __deleteScreenshot(self, type, screenshot_id):
        if len(self._uploadSessionData) == 0:
            raise 'Trying to delete screenshot without proper session keys'

        deleteScreenshotAction = self._uploadSessionData[type]['deleteURL']
        if deleteScreenshotAction != None:
            requests.get(ITUNESCONNECT_URL + deleteScreenshotAction + "?pictureId=" + screenshot_id
                    , cookies=cookie_jar)

            # TODO: check status


    def __sortScreenshots(self, type, newScreenshotsIndexes):
        if len(self._uploadSessionData) == 0:
            raise 'Trying to sort screenshots without proper session keys'

        sortScreenshotsAction = self._uploadSessionData[type]['sortURL']

        if sortScreenshotsAction != None:
            requests.get(ITUNESCONNECT_URL + sortScreenshotsAction 
                                    + "?sortedIDs=" + (",".join(newScreenshotsIndexes))
                            , cookies=cookie_jar)

            # TODO: check status

    def __parseAppVersionMetadata(self, version, language=None):
        tree = self._parser.parseTreeForURL(version['detailsLink'])

        AppMetadata = namedtuple('AppMetadata', ['activatedLanguages', 'nonactivatedLanguages'
                                                , 'formData', 'formNames', 'submitActions'])

        localizationLightboxAction = tree.xpath("//div[@id='localizationLightbox']/@action")[0] # if no lang provided, edit default
        #localizationLightboxUpdateAction = tree.xpath("//span[@id='localizationLightboxUpdate']/@action")[0] 

        activatedLanguages    = tree.xpath('//div[@id="modules-dropdown"] \
                                    /ul/li[count(preceding-sibling::li[@class="heading"])=1]/a/text()')
        nonactivatedLanguages = tree.xpath('//div[@id="modules-dropdown"] \
                                    /ul/li[count(preceding-sibling::li[@class="heading"])=2]/a/text()')
        
        activatedLanguages = [lng.replace("(Default)", "").strip() for lng in activatedLanguages]

        logging.info('Activated languages: ' + ', '.join(activatedLanguages))
        logging.debug('Nonactivated languages: ' + ', '.join(nonactivatedLanguages))

        langs = activatedLanguages

        if language != None:
            langs = [language]

        formData = {}
        formNames = {}
        submitActions = {}
        versionString = version['versionString']

        for lang in langs:
            logging.info('Processing language: ' + lang)
            languageId = languages.appleLangIdForLanguage(lang)
            logging.debug('Apple language id: ' + languageId)

            if lang in activatedLanguages:
                logging.info('Getting metadata for ' + lang + '. Version: ' + versionString)
            elif lang in nonactivatedLanguages:
                logging.info('Add ' + lang + ' for version ' + versionString)

            editTree = self._parser.parseTreeForURL(localizationLightboxAction + "?open=true" 
                                                        + ("&language=" + languageId if (languageId != None) else ""))
            hasWhatsNew = False

            formDataForLang = {}
            formNamesForLang = {}

            submitActionForLang = editTree.xpath("//div[@class='lcAjaxLightboxContentsWrapper']/div[@class='lcAjaxLightboxContents']/@action")[0]

            formNamesForLang['appNameName'] = editTree.xpath("//div[@id='appNameUpdateContainerId']//input/@name")[0]
            formNamesForLang['descriptionName'] = editTree.xpath("//div[@id='descriptionUpdateContainerId']//textarea/@name")[0]
            whatsNewName = editTree.xpath("//div[@id='whatsNewinthisVersionUpdateContainerId']//textarea/@name")

            if len(whatsNewName) > 0: # there's no what's new section for first version
                hasWhatsNew = True
                formNamesForLang['whatsNewName'] = whatsNewName[0]

            formNamesForLang['keywordsName']     = editTree.xpath("//div/label[.='Keywords']/..//input/@name")[0]
            formNamesForLang['supportURLName']   = editTree.xpath("//div/label[.='Support URL']/..//input/@name")[0]
            formNamesForLang['marketingURLName'] = editTree.xpath("//div/label[contains(., 'Marketing URL')]/..//input/@name")[0]
            formNamesForLang['pPolicyURLName']   = editTree.xpath("//div/label[contains(., 'Privacy Policy URL')]/..//input/@name")[0]

            formDataForLang['appNameValue']     = editTree.xpath("//div[@id='appNameUpdateContainerId']//input/@value")[0]
            formDataForLang['descriptionValue'] = getElement(editTree.xpath("//div[@id='descriptionUpdateContainerId']//textarea/text()"), 0)
            whatsNewValue    = editTree.xpath("//div[@id='whatsNewinthisVersionUpdateContainerId']//textarea/text()")

            if len(whatsNewValue) > 0 and hasWhatsNew:
                formDataForLang['whatsNewValue'] = getElement(whatsNewValue, 0)

            formDataForLang['keywordsValue']     = getElement(editTree.xpath("//div/label[.='Keywords']/..//input/@value"), 0)
            formDataForLang['supportURLValue']   = getElement(editTree.xpath("//div/label[.='Support URL']/..//input/@value"), 0)
            formDataForLang['marketingURLValue'] = getElement(editTree.xpath("//div/label[contains(., 'Marketing URL')]/..//input/@value"), 0)
            formDataForLang['pPolicyURLValue']   = getElement(editTree.xpath("//div/label[contains(., 'Privacy Policy URL')]/..//input/@value"), 0)

            logging.debug("Old values:")
            logging.debug(formDataForLang)

            iphoneUploadScreenshotForm = editTree.xpath("//form[@name='FileUploadForm_35InchRetinaDisplayScreenshots']")[0]
            iphone5UploadScreenshotForm = editTree.xpath("//form[@name='FileUploadForm_iPhone5']")[0]
            ipadUploadScreenshotForm = editTree.xpath("//form[@name='FileUploadForm_iPadScreenshots']")[0]

            formNamesForLang['iphoneUploadScreenshotForm'] = iphoneUploadScreenshotForm
            formNamesForLang['iphone5UploadScreenshotForm'] = iphone5UploadScreenshotForm
            formNamesForLang['ipadUploadScreenshotForm'] = ipadUploadScreenshotForm

            formData[languageId] = formDataForLang
            formNames[languageId] = formNamesForLang
            submitActions[languageId] = submitActionForLang

        metadata = AppMetadata(activatedLanguages=activatedLanguages
                             , nonactivatedLanguages=nonactivatedLanguages
                             , formData=formData
                             , formNames=formNames
                             , submitActions=submitActions)

        return metadata

    def __generateConfigForVersion(self, version):
        filename = str(self.applicationId) + '.json'
        languagesDict = {}

        metadata = self.__parseAppVersionMetadata(version)
        formData = metadata.formData
        # activatedLanguages = metadata.activatedLanguages

        for languageId, formValuesForLang in formData.items():
            langCode = languages.langCodeForLanguage(languageId)
            resultForLang = {}

            resultForLang["name"]               = formValuesForLang['appNameValue']
            resultForLang["whats new"]          = formValuesForLang.get('whatsNewValue')
            resultForLang["keywords"]           = formValuesForLang['keywordsValue']
            resultForLang["support url"]        = formValuesForLang['supportURLValue']
            resultForLang["marketing url"]      = formValuesForLang['marketingURLValue']
            resultForLang["privacy policy url"] = formValuesForLang['pPolicyURLValue']

            languagesDict[langCode] = resultForLang

        resultDict = {'config':{}, 'application': {'id': self.applicationId, 'metadata': {'general': {}, 'languages': languagesDict}}}
        with open(filename, 'wb') as fp:
            json.dump(resultDict, fp, sort_keys=True, indent=4, separators=(',', ': '))


    def generateConfig(self, versionString=None):
        if len(self.versions) == 0:
            self.getAppInfo()
        if len(self.versions) == 0:
            raise 'Can\'t get application versions'
        if versionString == None: # Suppose there's one or less editable versions
            versionString = next((versionString for versionString, version in self.versions.items() if version['editable']), None)
        if versionString == None: # No versions to edit. Generate config from the first one
            versionString = self.versions.keys()[0]
        
        self.__generateConfigForVersion(self.versions[versionString])


    def editVersion(self, dataDict, lang=None, versionString=None, filename_format=None):
        if dataDict == None or len(dataDict) == 0: # nothing to change
            return

        if len(self.versions) == 0:
            self.getAppInfo()
        if len(self.versions) == 0:
            raise 'Can\'t get application versions'
        if versionString == None: # Suppose there's one or less editable versions
            versionString = next((versionString for versionString, version in self.versions.items() if version['editable']), None)
        if versionString == None: # Suppose there's one or less editable versions
            raise 'No editable version found'
            
        version = self.versions[versionString]
        if not version['editable']:
            raise 'Version ' + versionString + ' is not editable'

        languageId = languages.appleLangIdForLanguage(lang)

        metadata = self.__parseAppVersionMetadata(version, lang)
        # activatedLanguages = metadata.activatedLanguages
        # nonactivatedLanguages = metadata.nonactivatedLanguages
        formData = metadata.formData[languageId]
        formNames = metadata.formNames[languageId]
        submitAction = metadata.submitActions[languageId]

        formData["save"] = "true"

        if 'name' in dataDict:
            formData[formNames['descriptionName']] = dataDict['name']

        if 'description' in dataDict:
            formData[formNames['appNameName']] = dataDict['description']

        if ('whatsNewName' in formNames) and ('whats new' in dataDict):
            formData[formNames['whatsNewName']] = dataDict['whats new']

        if 'keywords' in dataDict:
            formData[formNames['keywordsName']] = dataDict['keywords']

        if 'support url' in dataDict:
            formData[formNames['supportURLName']] = dataDict['support url']

        if 'marketing url' in dataDict:
            formData[formNames['marketingURLName']] = dataDict['marketing url']

        if 'privacy policy url' in dataDict:
            formData[formNames['pPolicyURLName']] = dataDict['privacy policy url']

        iphoneUploadScreenshotForm  = formNames['iphoneUploadScreenshotForm'] 
        iphone5UploadScreenshotForm = formNames['iphone5UploadScreenshotForm']
        ipadUploadScreenshotForm    = formNames['ipadUploadScreenshotForm']

        iphoneUploadScreenshotJS = iphoneUploadScreenshotForm.xpath('../following-sibling::script/text()')[0]
        iphone5UploadScreenshotJS = iphone5UploadScreenshotForm.xpath('../following-sibling::script/text()')[0]
        ipadUploadScreenshotJS = ipadUploadScreenshotForm.xpath('../following-sibling::script/text()')[0]

        self._uploadSessionData[DEVICE_TYPE.iPhone] = dict({'action': iphoneUploadScreenshotForm.attrib['action']
                                                        , 'key': iphoneUploadScreenshotForm.xpath(".//input[@name='uploadKey']/@value")[0]
                                                      }, **self.__parseURLSFromScript(iphoneUploadScreenshotJS))
        self._uploadSessionData[DEVICE_TYPE.iPhone5] = dict({'action': iphone5UploadScreenshotForm.attrib['action']
                                                         , 'key': iphone5UploadScreenshotForm.xpath(".//input[@name='uploadKey']/@value")[0]
                                                       }, **self.__parseURLSFromScript(iphone5UploadScreenshotJS))
        self._uploadSessionData[DEVICE_TYPE.iPad] = dict({'action': ipadUploadScreenshotForm.attrib['action']
                                                      , 'key': ipadUploadScreenshotForm.xpath(".//input[@name='uploadKey']/@value")[0]
                                                    }, **self.__parseURLSFromScript(ipadUploadScreenshotJS))

        self._uploadSessionId = iphoneUploadScreenshotForm.xpath('.//input[@name="uploadSessionID"]/@value')[0]

        # get all images
        for device_type in [DEVICE_TYPE.iPhone, DEVICE_TYPE.iPhone5, DEVICE_TYPE.iPad]:
            self._images[device_type] = self.__imagesForDevice(device_type)

        logging.debug(self._images)
        logging.debug(formData)

        if 'images' in dataDict:
            imagesActions = dataDict['images']
            languageCode = languages.langCodeForLanguage(lang)

            for dType in imagesActions:
                device_type = None
                if dType.lower() == 'iphone':
                    device_type = DEVICE_TYPE.iPhone
                elif dType.lower() == 'iphone 5':
                    device_type = DEVICE_TYPE.iPhone5
                elif dType.lower() == 'ipad':
                    device_type = DEVICE_TYPE.iPad
                else:
                    continue

                deviceImagesActions = imagesActions[dType]
                if deviceImagesActions == "":
                    continue

                for imageAction in deviceImagesActions:
                    imageAction.setdefault('cmd')
                    imageAction.setdefault('indexes')
                    cmd = imageAction['cmd']
                    indexes = imageAction['indexes']

                    imagePath = filename_format.replace('{language}', languageCode) \
                           .replace('{device_type}', DEVICE_TYPE.deviceStrings[device_type])
                    logging.debug('Looking for images at ' + imagePath)

                    if (indexes == None) and ((cmd == 'u') or (cmd == 'r')):
                        indexes = []
                        for i in range(0, 5):
                            realImagePath = imagePath.replace("{index}", str(i + 1))
                            if os.path.exists(realImagePath):
                                indexes.append(i + 1)

                    logging.debug('Processing command ' + imageAction.__str__())

                    if (cmd == 'd') or (cmd == 'r'): # delete or replace. To perform replace we need to delete images first
                        deleteIndexes = [img['id'] for img in self._images[device_type]]
                        if indexes != None:
                            deleteIndexes = [deleteIndexes[idx - 1] for idx in indexes]

                        for imageIndexToDelete in deleteIndexes:
                            img = next(im for im in self._images[DEVICE_TYPE.iPhone5] if im['id'] == imageIndexToDelete)
                            self.__deleteScreenshot(DEVICE_TYPE.iPhone5, img['id'])

                        self._images[device_type] = self.__imagesForDevice(device_type)
                    
                    if (cmd == 'u') or (cmd == 'r'): # upload or replace
                        currentIndexes = [img['id'] for img in self._images[device_type]]

                        if indexes == None:
                            continue

                        indexes = sorted(indexes)
                        for i in indexes:
                            realImagePath = imagePath.replace("{index}", str(i))
                            if os.path.exists(realImagePath):
                                self.__uploadScreenshot(device_type, realImagePath)

                        self._images[device_type] = self.__imagesForDevice(device_type)

                        if cmd == 'r':
                            newIndexes = [img['id'] for img in self._images[device_type]][len(currentIndexes):]

                            if len(newIndexes) == 0:
                                continue

                            for i in indexes:
                                currentIndexes.insert(i - 1, newIndexes.pop(0))

                            self.__sortScreenshots(device_type, currentIndexes)
                            self._images[device_type] = self.__imagesForDevice(device_type)

                    if (cmd == 's'): # sort
                        if indexes == None or len(indexes) != len(self._images[device_type]):
                            continue
                        newIndexes = [self._images[device_type][i - 1]['id'] for i in indexes]

                        self.__sortScreenshots(device_type, newIndexes)
                        self._images[device_type] = self.__imagesForDevice(device_type)

        formData['uploadSessionID'] = self._uploadSessionId
        # formData['uploadKey'] = self._uploadSessionData[DEVICE_TYPE.iPhone5]['key']

        postFormResponse = requests.post(ITUNESCONNECT_URL + submitAction, data = formData, cookies=cookie_jar)

        if postFormResponse.status_code != 200:
            raise 'Wrong response from iTunesConnect. Status code: ' + str(postFormResponse.status_code)

        if len(postFormResponse.text) > 0:
            logging.error("Save information failed. " + postFormResponse.text)


    def __parseInappActionURLsFromScript(self, script):
        matches = re.findall('\'([^\']+)\'\s:\s\'([^\']+)\'', script)
        self._inappActionURLs = dict((k, v) for k, v in matches if k.endswith('Url'))
        ITCInappPurchase.actionURLs = self._inappActionURLs

        return self._inappActionURLs


    def __parseInappsFromTree(self, refreshContainerTree):
        logging.debug('Parsing inapps response')
        inappULs = refreshContainerTree.xpath('.//li[starts-with(@id, "ajaxListRow_")]')

        if len(inappULs) == 0:
            logging.info('No In-App Purchases found')
            return None

        logging.debug('Found ' + str(len(inappULs)) + ' inapps')

        inappsActionScript = refreshContainerTree.xpath('//script[contains(., "var arguments")]/text()')
        if len(inappsActionScript) > 0:
            inappsActionScript = inappsActionScript[0]
            actionURLs = self.__parseInappActionURLsFromScript(inappsActionScript)
            inappsItemAction = actionURLs['itemActionUrl']

        inapps = {}
        for inappUL in inappULs:
            appleId = inappUL.xpath('./div/div[5]/text()')[0].strip()
            if self.inapps.get(appleId) != None:
                inapps[appleId] = self.inapps.get(appleId)
                continue

            iaptype = inappUL.xpath('./div/div[4]/text()')[0].strip()  
            if not (iaptype in ITCInappPurchase.supportedIAPTypes):
                continue

            numericId = inappUL.xpath('./div[starts-with(@class,"ajaxListRowDiv")]/@itemid')[0]
            name = inappUL.xpath('./div/div/span/text()')[0].strip()
            productId = inappUL.xpath('./div/div[3]/text()')[0].strip()
            manageLink = inappsItemAction + "?itemID=" + numericId
            inapps[appleId] = ITCInappPurchase(name=name, appleId=appleId, numericId=numericId, productId=productId, iaptype=iaptype, manageLink=manageLink)

        return inapps


    def getInapps(self):
        if self._manageInappsLink == None:
            self.getAppInfo()
        if self._manageInappsLink == None:
            raise 'Can\'t get "Manage In-App purchases link :(("'

        # TODO: parse multiple pages of inapps.
        tree = self._parser.parseTreeForURL(self._manageInappsLink)

        self._createInappLink = tree.xpath('//img[contains(@src, "btn-create-new-in-app-purchase.png")]/../@href')[0]
        if ITCInappPurchase.createInappLink == None:
            ITCInappPurchase.createInappLink = self._createInappLink

        refreshContainerTree = tree.xpath('//span[@id="ajaxListListRefreshContainerId"]/ul')[0]
        self.inapps = self.__parseInappsFromTree(refreshContainerTree)


    def getInappById(self, inappId):
        if self._inappActionURLs == None:
            self.getInapps()

        if type(inappId) is int:
            inappId = str(inappId)

        if self.inapps.get(inappId) != None:
            return self.inapps[inappId]

        if self._manageInappsTree == None:
            self._manageInappsTree = self._parser.parseTreeForURL(self._manageInappsLink)

        tree = self._manageInappsTree
        reloadInappsAction = tree.xpath('//span[@id="ajaxListListRefreshContainerId"]/@action')[0]
        searchAction = self._inappActionURLs['searchActionUrl']

        logging.info('Searching for inapp with id ' + inappId)

        searchResponse = requests.get(ITUNESCONNECT_URL + searchAction + "?query=" + inappId, cookies=cookie_jar)

        if searchResponse.status_code != 200:
            raise 'Wrong response from iTunesConnect. Status code: ' + str(searchResponse.status_code)

        statusJSON = json.loads(searchResponse.content)
        if statusJSON['totalItems'] <= 0:
            logging.warn('No matching inapps found! Search term: ' + inappId)
            return None

        inapps = self.__parseInappsFromTree(self._parser.parseTreeForURL(reloadInappsAction))

        if inapps == None:
            raise "Error parsing inapps"

        if len(inapps) == 1:
            return inapps[0]

        tmpinapps = []
        for numericId, inapp in inapps.items():
            if (inapp.numericId == inappId) or (inapp.productId == inappId):
                return inapp

            components = inapp.productId.partition(u'…')
            if components[1] == u'…': #split successful
                if inappId.startswith(components[0]) and inappId.endswith(components[2]):
                    tmpinapps.append(inapp)

        if len(tmpinapps) == 1:
            return tmpinapps[0]

        logging.error('Multiple inapps found for id (' + inappId + ').')
        logging.error(tmpinapps)

        # TODO: handle this situation. It is possible to avoid this exception by requesting
        # each result's page. Possible, but expensive :)
        raise 'Ambiguous search result.'


    def createInapp(self, inappDict):
        if self._createInappLink == None:
            self.getInapps()
        if self._createInappLink == None:
            raise 'Can\'t create inapp purchase'

        if not (inappDict['type'] in ITCInappPurchase.supportedIAPTypes):
            logging.error('Can\'t create inapp purchase: "' + inappDict['id'] + '" is not supported')
            return

        iap = ITCInappPurchase(name=inappDict['reference name']
                             , productId=inappDict['id']
                             , iaptype=inappDict['type'])
        iap.clearedForSale = inappDict['cleared']
        iap.priceTier = int(inappDict['price tier']) - 1
        iap.hostingContentWithApple = inappDict['hosting content with apple']
        iap.reviewNotes = inappDict['review notes']

        iap.create(inappDict['languages'])
